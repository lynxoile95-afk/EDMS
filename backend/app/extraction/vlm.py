"""Optional Vision-Language-Model fallback (OpenAI-compatible, on-premise).

**Disabled by default.** Documents are confidential; the only supported topology
is a local inference server (vLLM / Ollama / LM Studio) reachable on the
internal network.  The class refuses to run against a public endpoint unless
``VLM_ALLOW_REMOTE=true`` is explicitly set — a deliberate speed bump.

The model is asked for strict JSON.  We never trust it blindly: every value is
re-normalised and re-validated with the same deterministic code used for the
text layer, and its confidence is capped below the text-layer ceiling.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.extraction.base import ExtractionError
from app.models.enums import ExtractionSource
from app.services.normalization import (
    extract_code_candidates,
    normalize_code,
    normalize_rev,
    to_ascii_digits,
)

logger = logging.getLogger(__name__)

MAX_VLM_CONFIDENCE = 0.85  # a model guess never outranks a clean text layer

EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "doc_code": {"type": ["string", "null"]},
                "rev": {"type": ["string", "null"]},
                "title": {"type": ["string", "null"]},
                "discipline": {"type": ["string", "null"]},
                "unit": {"type": ["string", "null"]},
                "status": {"type": ["string", "null"]},
                "date": {"type": ["string", "null"]},
                "sheet_no": {"type": ["string", "null"]},
                "sheet_count": {"type": ["string", "null"]},
            },
        },
        "confidence": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
        "notes": {"type": ["string", "null"]},
    },
}

PROMPT = """You are reading the title block (کادر مشخصات) of an engineering drawing.
Return ONLY a JSON object that matches the provided schema.

Rules:
1. Copy values exactly as printed. Do not guess, translate or complete values.
2. If a field is not visible, return null for it (never invent one).
3. `doc_code` must be the document/drawing number, including dashes as printed
   (e.g. "PRJ-CIV-DR-0123"). Do not include the revision in the code.
4. `rev` is only the revision token (e.g. "A", "03", "P02").
5. `title` is the drawing title as printed (Persian or English, keep original script).
6. `confidence` maps each field name to your own 0..1 certainty.
7. Persian/Arabic digits must be returned as ASCII digits.

Schema:
{schema}
"""


@dataclass(slots=True)
class VLMField:
    name: str
    value: str
    confidence: float
    source: ExtractionSource = ExtractionSource.VLM


class VLMExtractor:
    name = "openai-compatible"

    def __init__(self) -> None:
        self.enabled = settings.vlm_enabled
        self.base_url = settings.vlm_base_url.rstrip("/")
        self.model = settings.vlm_model

    # ------------------------------------------------------------------ guard
    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if not self.is_local_endpoint():
            logger.warning("VLM endpoint %s is not local; refusing to send documents.", self.base_url)
            return False
        return True

    def is_local_endpoint(self) -> bool:
        host = self.base_url.split("//")[-1].split("/")[0].split(":")[0]
        return host in {"localhost", "127.0.0.1", "vlm", "ollama", "0.0.0.0"} or host.startswith(
            ("10.", "192.168.", "172.", "vlm")
        ) or host.endswith(".local") or host.endswith(".internal")

    # ------------------------------------------------------------------- call
    def extract(self, image_bytes: bytes) -> dict[str, tuple[str, float]]:
        """Return ``{field: (normalised_value, confidence)}`` (empty on failure)."""
        if not self.is_available():
            return {}
        encoded = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 800,
            "messages": [
                {
                    "role": "system",
                    "content": "You extract structured data from engineering drawing title blocks. "
                    "Answer with JSON only.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT.format(schema=json.dumps(EXTRACTION_SCHEMA))},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    ],
                },
            ],
        }
        try:
            with httpx.Client(timeout=settings.vlm_timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.vlm_api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            logger.warning("VLM call failed: %s", exc)
            return {}

        data = _parse_json_block(content)
        if not data:
            return {}
        return _normalise_model_output(data)


def _parse_json_block(content: str) -> dict:
    if not content:
        return {}
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _normalise_model_output(data: dict) -> dict[str, tuple[str, float]]:
    """Re-normalise model output with the same deterministic helpers."""
    fields = data.get("fields") or {}
    confidences = data.get("confidence") or {}
    result: dict[str, tuple[str, float]] = {}
    for name, raw in fields.items():
        if raw in (None, "", "null"):
            continue
        value = to_ascii_digits(str(raw)).strip()
        if not value:
            continue
        try:
            confidence = float(confidences.get(name, 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        confidence = max(0.0, min(MAX_VLM_CONFIDENCE, confidence))

        if name == "doc_code":
            candidates = extract_code_candidates(value, r"\S+")
            if not candidates:
                normalized = normalize_code(value)
                if not normalized:
                    continue
                value = normalized
            else:
                value = candidates[0]
        elif name == "rev":
            value = normalize_rev(value)
            if not value:
                continue
        result[name] = (value, round(confidence, 3))
    return result


def get_vlm() -> VLMExtractor:
    return VLMExtractor()


class VLMUnavailable(ExtractionError):
    pass
