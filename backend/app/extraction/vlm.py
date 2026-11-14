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

import json
import logging
from dataclasses import dataclass

import httpx
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
    """Provider-agnostic VLM facade used by the extraction pipeline.

    The active persisted credential is resolved server-side for every extractor
    instance. If the credential store is empty or not migrated yet, the existing
    VLM_* environment settings remain a supported fallback.
    """

    name = "provider-adapter"

    def __init__(self) -> None:
        from app.ai.service import active_runtime, fallback_runtime

        self.runtime = active_runtime()
        self.fallback_runtime = fallback_runtime()
        self.enabled = self.runtime.enabled
        self.base_url = self.runtime.base_url.rstrip("/")
        self.model = self.runtime.model
        self.last_model = self.model
        # Kept injectable so the existing extraction tests can mock provider HTTP
        # calls without allowing a real network request.
        self._client_factory = httpx.Client

    @property
    def provider(self) -> str:
        return self.runtime.provider_id

    @staticmethod
    def _is_local_runtime(runtime) -> bool:
        from app.ai.registry import get_provider

        host = runtime.base_url.split("//")[-1].split("/")[0].split(":")[0]
        catalog = get_provider(runtime.provider_id) or {}
        return bool(catalog.get("isLocal")) or host in {"localhost", "127.0.0.1", "vlm", "ollama", "0.0.0.0"} or host.startswith(
            ("10.", "192.168.", "172.", "vlm")
        ) or host.endswith(".local") or host.endswith(".internal")

    @classmethod
    def _runtime_is_trusted(cls, runtime) -> bool:
        """Local providers are safe; cloud providers require explicit consent."""
        return cls._is_local_runtime(runtime) or bool(runtime.remote_consent)

    @staticmethod
    def _runtime_has_key(runtime) -> bool:
        from app.ai.registry import get_provider

        catalog = get_provider(runtime.provider_id) or {}
        return bool(runtime.api_key) or not bool(catalog.get("keyRequired", True))

    def _runtime_is_available(self, runtime) -> bool:
        return bool(runtime.enabled) and self._runtime_is_trusted(runtime) and self._runtime_has_key(runtime)

    def is_local_endpoint(self) -> bool:
        return self._is_local_runtime(self.runtime)

    def endpoint_trusted(self) -> bool:
        return self._runtime_is_trusted(self.runtime)

    def is_available(self) -> bool:
        return any(self._runtime_is_available(runtime) for runtime in self._runtimes())

    def _runtimes(self):
        yield self.runtime
        if self.fallback_runtime and self.fallback_runtime.credential_id != self.runtime.credential_id:
            yield self.fallback_runtime

    def _adapter(self, runtime=None):
        selected = runtime or self.runtime
        if not self._runtime_is_trusted(selected):
            raise RuntimeError("ارسال تصویر به سرویس عمومی بدون تأیید حریم خصوصی مجاز نیست.")
        return selected.adapter(self._client_factory)

    def _call_raw(self, image_bytes: bytes, prompt: str) -> str:
        last_error: Exception | None = None
        for runtime in self._runtimes():
            if not self._runtime_is_available(runtime):
                continue
            try:
                content = self._adapter(runtime).vlm_extract(image_bytes, prompt)
                if not content.strip():
                    raise ValueError("پاسخ سرویس هوش مصنوعی خالی بود.")
                self.last_model = runtime.model
                return content
            except (httpx.HTTPError, KeyError, IndexError, ValueError, RuntimeError) as exc:
                last_error = exc
                logger.warning("VLM provider %s failed: %s", runtime.provider_id, str(exc)[:300])
        if last_error is not None:
            raise last_error
        raise RuntimeError("هیچ Credential فعال و قابل استفاده‌ای برای VLM وجود ندارد.")

    # ------------------------------------------------------------------- call
    def extract(self, image_bytes: bytes) -> dict[str, tuple[str, float]]:
        """Return ``{field: (normalised_value, confidence)}`` (empty on failure)."""
        if not self.is_available():
            return {}
        try:
            content = self._call_raw(image_bytes, self._prompt())
        except (httpx.HTTPError, KeyError, IndexError, ValueError, RuntimeError) as exc:
            logger.warning("VLM call failed: %s", str(exc)[:300])
            return {}

        data = _parse_json_block(content)
        if not data:
            return {}
        return _normalise_model_output(data)

    def extract_text(self, image_bytes: bytes, prompt: str) -> str:
        """Send a raw provider-agnostic VLM prompt, using the optional fallback."""
        if not self.is_available():
            raise RuntimeError("هیچ Credential فعال و قابل استفاده‌ای برای VLM وجود ندارد.")
        return self._call_raw(image_bytes, prompt)

    def _prompt(self) -> str:
        return PROMPT.format(schema=json.dumps(EXTRACTION_SCHEMA))

    # Compatibility helpers for callers/tests that used the old provider methods.
    def _call_openai(self, encoded: str) -> str:
        import base64

        return self._adapter().vlm_extract(base64.b64decode(encoded), self._prompt())

    def _call_gemini(self, encoded: str) -> str:
        import base64

        return self._adapter().vlm_extract(base64.b64decode(encoded), self._prompt())

    # ------------------------------------------------------------------- probe
    def probe(self) -> dict:
        """Test the active provider without returning its API key."""
        from app.ai.service import result_payload, test_credential

        result: dict = {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "key_set": bool(self.runtime.api_key),
            "enabled": self.enabled,
            "endpoint_local": self.is_local_endpoint(),
            "remote_allowed": bool(self.runtime.remote_consent),
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
        from app.ai.registry import get_provider

        catalog = get_provider(self.provider) or {}
        if catalog.get("keyRequired") and not self.runtime.api_key:
            result["error"] = "کلید API تنظیم نشده است."
            return result
        tested = result_payload(test_credential(self.runtime, client_factory=self._client_factory))
        result["ok"] = tested["ok"]
        result["status_code"] = tested["status"]
        result["latency_ms"] = tested["latencyMs"]
        result["error"] = None if tested["ok"] else tested["message"]
        result["models_seen"] = tested.get("models")
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
