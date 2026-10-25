"""Optional Vision-Language-Model fallback.

**Disabled by default.** Documents are confidential; the preferred topology is a
local inference server (vLLM / Ollama / LM Studio) reachable on the internal
network. The class refuses to run against a public endpoint unless
``vlm_allow_remote`` has been explicitly confirmed by an administrator (see
``PUT /admin/ai-config``) — a deliberate speed bump, because enabling it means
drawing crops leave the organisation.

Two providers speak the same "strict JSON, never trusted blindly" contract:

* ``openai`` — any OpenAI-compatible server (``POST {base}/chat/completions``).
* ``gemini`` — Google Generative Language API
  (``POST {base}/models/{model}:generateContent``).

Either way the answer is re-normalised and re-validated with the deterministic
helpers, and its confidence is capped below the text-layer ceiling.

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
        if not self.endpoint_trusted():
            logger.warning(
                "VLM endpoint %s is not local and remote use is not confirmed; "
                "refusing to send documents.",
                self.base_url,
            )
            return False
        return True

    @property
    def provider(self) -> str:
        return (settings.vlm_provider or "openai").lower()

    def is_local_endpoint(self) -> bool:
        host = self.base_url.split("//")[-1].split("/")[0].split(":")[0]
        return host in {"localhost", "127.0.0.1", "vlm", "ollama", "0.0.0.0"} or host.startswith(
            ("10.", "192.168.", "172.", "vlm")
        ) or host.endswith(".local") or host.endswith(".internal")

    def endpoint_trusted(self) -> bool:
        """Local, or a public endpoint an administrator explicitly confirmed."""
        return self.is_local_endpoint() or bool(settings.vlm_allow_remote)

    # ------------------------------------------------------------------- call
    def extract(self, image_bytes: bytes) -> dict[str, tuple[str, float]]:
        """Return ``{field: (normalised_value, confidence)}`` (empty on failure)."""
        if not self.is_available():
            return {}
        encoded = base64.b64encode(image_bytes).decode("ascii")
        try:
            if self.provider == "gemini":
                content = self._call_gemini(encoded)
            else:
                content = self._call_openai(encoded)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            logger.warning("VLM call failed: %s", exc)
            return {}

        data = _parse_json_block(content)
        if not data:
            return {}
        return _normalise_model_output(data)

    # --------------------------------------------------------------- providers
    def _prompt(self) -> str:
        return PROMPT.format(schema=json.dumps(EXTRACTION_SCHEMA))

    def _call_openai(self, encoded: str) -> str:
        """Any OpenAI-compatible server: vLLM, Ollama, LM Studio, …"""
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
                        {"type": "text", "text": self._prompt()},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    ],
                },
            ],
        }
        with httpx.Client(timeout=settings.vlm_timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.vlm_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    def _call_gemini(self, encoded: str) -> str:
        """Google Generative Language API (``generateContent``).

        The key travels in ``x-goog-api-key`` rather than the query string, so it
        cannot end up in a proxy access log.
        """
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self._prompt()},
                        {"inline_data": {"mime_type": "image/png", "data": encoded}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 800,
                "responseMimeType": "application/json",
            },
        }
        with httpx.Client(timeout=settings.vlm_timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": settings.vlm_api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        parts = body["candidates"][0]["content"]["parts"]
        return "\n".join(part.get("text", "") for part in parts)

    # ------------------------------------------------------------------- probe
    def probe(self) -> dict:
        """Does the endpoint answer at all? Never raises, never returns the key.

        Used by the «آزمایش اتصال» button on the upload screen so a failure can be
        explained (DNS, refused, 401, wrong model) instead of silently leaving the
        AI layer off.
        """
        import time

        result: dict = {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "key_set": bool(settings.vlm_api_key and settings.vlm_api_key != "EMPTY"),
            "enabled": self.enabled,
            "endpoint_local": self.is_local_endpoint(),
            "remote_allowed": bool(settings.vlm_allow_remote),
            "ok": False,
            "status_code": None,
            "latency_ms": None,
            "error": None,
            "models_seen": None,
        }
        if not self.enabled:
            result["error"] = "لایه VLM در تنظیمات فعال نشده است (enabled=false)."
            return result
        if not self.endpoint_trusted():
            result["error"] = (
                "آدرس VLM داخلی نیست و ارسال به بیرون تأیید نشده است؛ «تأیید ارسال به سرور عمومی» را تیک بزنید."
            )
            return result
        if not result["key_set"]:
            result["error"] = "کلید API تنظیم نشده است."

        headers: dict[str, str] = {}
        if self.provider == "gemini":
            url = f"{self.base_url}/models"
            headers["x-goog-api-key"] = settings.vlm_api_key
        else:
            url = f"{self.base_url}/models"
            headers["Authorization"] = f"Bearer {settings.vlm_api_key}"

        started = time.monotonic()
        try:
            with httpx.Client(timeout=min(settings.vlm_timeout_seconds, 20)) as client:
                response = client.get(url, headers=headers)
            result["latency_ms"] = int((time.monotonic() - started) * 1000)
            result["status_code"] = response.status_code
            if response.status_code >= 400:
                result["error"] = _explain_http_error(response.status_code, response.text)
                return result
            try:
                body = response.json()
                names = [row.get("name") or row.get("id") for row in body.get("models", body.get("data", []))]
                result["models_seen"] = [name for name in names if name][:40]
            except ValueError:
                result["models_seen"] = None
            result["ok"] = True
            result["error"] = None
            return result
        except httpx.HTTPError as exc:
            result["latency_ms"] = int((time.monotonic() - started) * 1000)
            result["error"] = _explain_transport_error(exc, self.base_url)
            return result


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


def _explain_http_error(status_code: int, body: str) -> str:
    """Turn a provider status into something an operator can act on."""
    snippet = (body or "").strip().replace("\n", " ")[:180]
    if status_code in (401, 403):
        return f"کلید API پذیرفته نشد (HTTP {status_code}). کلید یا دسترسی پروژه را بررسی کنید. {snippet}"
    if status_code == 404:
        return f"آدرس یا نام مدل پیدا نشد (HTTP 404). base_url و model را بررسی کنید. {snippet}"
    if status_code == 429:
        return f"سهمیه/نرخ درخواست تمام شده است (HTTP 429). {snippet}"
    return f"پاسخ ناموفق از سرویس هوش مصنوعی (HTTP {status_code}). {snippet}"


def _explain_transport_error(exc: Exception, base_url: str) -> str:
    """Explain *why* the connection failed — usually egress, not the key."""
    text = str(exc)
    if isinstance(exc, httpx.ConnectError):
        return (
            f"اتصال به {base_url} برقرار نشد (DNS یا مسدود بودن خروجی شبکه). "
            f"جزئیات: {text[:160]}"
        )
    if isinstance(exc, httpx.TimeoutException):
        return f"پاسخی از {base_url} در زمان مجاز نیامد. جزئیات: {text[:160]}"
    return f"خطای شبکه هنگام تماس با {base_url}: {text[:160]}"


def get_vlm() -> VLMExtractor:
    return VLMExtractor()


class VLMUnavailable(ExtractionError):
    pass
