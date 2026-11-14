"""Provider adapters used by both connection tests and the extraction pipeline."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.ai.registry import get_provider

ClientFactory = Callable[..., Any]


@dataclass(slots=True)
class ProviderCredential:
    provider_id: str
    api_type: str
    base_url: str
    model: str
    api_key: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConnectionResult:
    ok: bool
    status: int | None
    message: str
    latency_ms: int | None
    model: str
    models: list[str] | None = None
    raw_error: str | None = None


class ProviderRequestError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, raw: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.raw = raw or ""


class AIAdapter:
    """Common adapter contract: test, list models and vision extraction."""

    def __init__(self, credential: ProviderCredential, client_factory: ClientFactory | None = None) -> None:
        self.credential = credential
        self.client_factory = client_factory or httpx.Client

    @property
    def catalog(self) -> dict[str, Any]:
        return get_provider(self.credential.provider_id) or {}

    def _client(self, timeout: float = 15.0):
        return self.client_factory(timeout=timeout, follow_redirects=True)

    def test_connection(self) -> ConnectionResult:
        started = time.perf_counter()
        try:
            models = self._test_request()
            return ConnectionResult(
                ok=True,
                status=200,
                message="اتصال برقرار است — Connection tested successfully",
                latency_ms=_latency(started),
                model=self.credential.model,
                models=models,
            )
        except ProviderRequestError as exc:
            return ConnectionResult(
                ok=False,
                status=exc.status,
                message=exc.args[0],
                latency_ms=_latency(started),
                model=self.credential.model,
                raw_error=_redact_error(exc.raw or str(exc), self.credential.api_key),
            )
        except httpx.TimeoutException as exc:
            return ConnectionResult(
                ok=False,
                status=None,
                message="زمان اتصال تمام شد؛ سرویس یا شبکه پاسخ نداد.",
                latency_ms=_latency(started),
                model=self.credential.model,
                raw_error=_redact_error(str(exc), self.credential.api_key),
            )
        except httpx.HTTPError as exc:
            return ConnectionResult(
                ok=False,
                status=None,
                message="اتصال شبکه برقرار نشد؛ آدرس سرویس، DNS و دسترسی خروجی را بررسی کنید.",
                latency_ms=_latency(started),
                model=self.credential.model,
                raw_error=_redact_error(str(exc), self.credential.api_key),
            )
        except Exception as exc:  # noqa: BLE001 - provider-specific failures become UI state
            return ConnectionResult(
                ok=False,
                status=None,
                message="آزمایش اتصال با خطای پیش‌بینی‌نشده مواجه شد.",
                latency_ms=_latency(started),
                model=self.credential.model,
                raw_error=_redact_error(str(exc), self.credential.api_key),
            )

    def list_models(self) -> list[str]:
        """Return live model IDs where the provider exposes a model endpoint."""
        raise ProviderRequestError("این سرویس فهرست مدل قابل دریافت ندارد.")

    def vlm_extract(self, image_bytes: bytes, prompt: str) -> str:
        raise NotImplementedError

    # Keep the interface discoverable for integrations that use the JavaScript-
    # style names from the provider registry specification.
    def testConnection(self) -> ConnectionResult:  # noqa: N802
        return self.test_connection()

    def listModels(self) -> list[str]:  # noqa: N802
        return self.list_models()

    def vlmExtract(self, image_bytes: bytes, prompt: str) -> str:  # noqa: N802
        return self.vlm_extract(image_bytes, prompt)

    def _test_request(self) -> list[str] | None:
        raise NotImplementedError

    def _response_json(self, response: Any) -> dict[str, Any]:
        status = int(getattr(response, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            body = _response_text(response)
            raise ProviderRequestError(
                _human_http_error(status, body, self.credential.api_key),
                status=status,
                raw=body,
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise ProviderRequestError("پاسخ سرویس JSON معتبر نبود.", status=status, raw=_response_text(response)) from exc
        if not isinstance(payload, dict):
            raise ProviderRequestError("پاسخ سرویس ساختار معتبری نداشت.", status=status)
        return payload

    def _post_json(self, url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        with self._client() as client:
            response = client.post(url, headers=headers, json=payload)
        return self._response_json(response)

    def _get_json(self, url: str, *, headers: dict[str, str]) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(url, headers=headers)
        return self._response_json(response)


class OpenAICompatibleAdapter(AIAdapter):
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.credential.api_key:
            headers["Authorization"] = f"Bearer {self.credential.api_key}"
        return headers

    def list_models(self) -> list[str]:
        body = self._get_json(f"{self.credential.base_url.rstrip('/')}/models", headers=self._headers())
        data = body.get("data", [])
        if not isinstance(data, list):
            return []
        return sorted({str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")})

    def _chat(self, *, image_bytes: bytes | None = None, prompt: str = "ping") -> dict[str, Any]:
        content: Any = prompt
        if image_bytes is not None:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ]
        payload = {
            "model": self.credential.model,
            "temperature": 0,
            "max_tokens": 5 if image_bytes is None else 800,
            "messages": [
                {"role": "system", "content": "You extract structured data from engineering drawing title blocks. Answer with JSON only."},
                {"role": "user", "content": content},
            ],
        }
        return self._post_json(
            f"{self.credential.base_url.rstrip('/')}/chat/completions",
            headers=self._headers(),
            payload=payload,
        )

    def _test_request(self) -> list[str] | None:
        # A model-list call is cheaper and gives a better wrong-key/base-URL error;
        # chat is still performed so custom providers that omit /models are testable.
        if self.catalog.get("supportsModelList"):
            try:
                models = self.list_models()
            except ProviderRequestError as exc:
                if exc.status not in {404, 405}:
                    raise
            else:
                if self.credential.model and models and self.credential.model not in models:
                    raise ProviderRequestError(
                        "مدل انتخاب‌شده در فهرست این سرویس وجود ندارد (HTTP 404). نام مدل را بررسی کنید.",
                        status=404,
                    )
                if models:
                    return models
        self._chat()
        return None

    def vlm_extract(self, image_bytes: bytes, prompt: str) -> str:
        body = self._chat(image_bytes=image_bytes, prompt=prompt)
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError("پاسخ مدل ساختار chat completions نداشت.", raw=json.dumps(body)[:600]) from exc


class GeminiAdapter(AIAdapter):
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.credential.api_key:
            headers["x-goog-api-key"] = self.credential.api_key
        return headers

    def _generate(self, *, image_bytes: bytes | None = None, prompt: str = "ping") -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        if image_bytes is not None:
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image_bytes).decode("ascii")}})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 5 if image_bytes is None else 800,
                "responseMimeType": "application/json" if image_bytes is not None else "text/plain",
            },
        }
        url = f"{self.credential.base_url.rstrip('/')}/models/{self.credential.model}:generateContent"
        return self._post_json(url, headers=self._headers(), payload=payload)

    def list_models(self) -> list[str]:
        body = self._get_json(f"{self.credential.base_url.rstrip('/')}/models", headers=self._headers())
        models = body.get("models", [])
        if not isinstance(models, list):
            return []
        return sorted({str(item.get("name", "")).removeprefix("models/") for item in models if isinstance(item, dict) and item.get("name")})

    def _test_request(self) -> list[str] | None:
        try:
            models = self.list_models()
        except ProviderRequestError as exc:
            if exc.status not in {404, 405}:
                raise
            self._generate()
            return None
        if self.credential.model and models and self.credential.model not in models:
            raise ProviderRequestError(
                "مدل انتخاب‌شده در فهرست Gemini وجود ندارد (HTTP 404). نام مدل را بررسی کنید.",
                status=404,
            )
        return models

    def vlm_extract(self, image_bytes: bytes, prompt: str) -> str:
        body = self._generate(image_bytes=image_bytes, prompt=prompt)
        try:
            parts = body["candidates"][0]["content"]["parts"]
            return "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError("پاسخ Gemini ساختار generateContent نداشت.", raw=json.dumps(body)[:600]) from exc


class AnthropicAdapter(AIAdapter):
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.credential.api_key,
            "anthropic-version": "2023-06-01",
        }

    def _messages(self, *, image_bytes: bytes | None = None, prompt: str = "ping") -> dict[str, Any]:
        content: Any = prompt
        if image_bytes is not None:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(image_bytes).decode("ascii")}},
            ]
        return self._post_json(
            f"{self.credential.base_url.rstrip('/')}/messages",
            headers=self._headers(),
            payload={"model": self.credential.model, "max_tokens": 5 if image_bytes is None else 800, "messages": [{"role": "user", "content": content}]},
        )

    def _test_request(self) -> list[str] | None:
        self._messages()
        return None

    def vlm_extract(self, image_bytes: bytes, prompt: str) -> str:
        body = self._messages(image_bytes=image_bytes, prompt=prompt)
        try:
            return "\n".join(str(item.get("text", "")) for item in body["content"] if item.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise ProviderRequestError("پاسخ Anthropic ساختار messages نداشت.", raw=json.dumps(body)[:600]) from exc


class AzureOpenAIAdapter(OpenAICompatibleAdapter):
    def _azure_url(self) -> str:
        deployment = str(self.credential.extra.get("deployment", "")).strip()
        api_version = str(self.credential.extra.get("api_version", "")).strip()
        if not deployment or not api_version:
            raise ProviderRequestError("نام deployment و API version برای Azure الزامی است.")
        return f"{self.credential.base_url.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "api-key": self.credential.api_key}

    def _chat(self, *, image_bytes: bytes | None = None, prompt: str = "ping") -> dict[str, Any]:
        content: Any = prompt
        if image_bytes is not None:
            content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"}}]
        return self._post_json(
            self._azure_url(),
            headers=self._headers(),
            payload={"messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 5 if image_bytes is None else 800},
        )

    def _test_request(self) -> list[str] | None:
        self._chat()
        return None


class OllamaAdapter(AIAdapter):
    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def list_models(self) -> list[str]:
        body = self._get_json(f"{self.credential.base_url.rstrip('/')}/api/tags", headers=self._headers())
        models = body.get("models", [])
        return sorted({str(item.get("name")) for item in models if isinstance(item, dict) and item.get("name")})

    def _chat(self, *, image_bytes: bytes | None = None, prompt: str = "ping") -> dict[str, Any]:
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if image_bytes is not None:
            message["images"] = [base64.b64encode(image_bytes).decode("ascii")]
        return self._post_json(
            f"{self.credential.base_url.rstrip('/')}/api/chat",
            headers=self._headers(),
            payload={"model": self.credential.model, "messages": [message], "stream": False, "options": {"num_predict": 5 if image_bytes is None else 800}},
        )

    def _test_request(self) -> list[str] | None:
        try:
            models = self.list_models()
        except ProviderRequestError as exc:
            if exc.status not in {404, 405}:
                raise
            self._chat()
            return None
        if self.credential.model and models and self.credential.model not in models:
            raise ProviderRequestError(
                "مدل انتخاب‌شده در Ollama وجود ندارد (HTTP 404). نام مدل را بررسی کنید.",
                status=404,
            )
        return models

    def vlm_extract(self, image_bytes: bytes, prompt: str) -> str:
        body = self._chat(image_bytes=image_bytes, prompt=prompt)
        try:
            return str(body["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise ProviderRequestError("پاسخ Ollama ساختار chat نداشت.", raw=json.dumps(body)[:600]) from exc


class CohereAdapter(AIAdapter):
    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.credential.api_key}"}

    def _chat(self, *, image_bytes: bytes | None = None, prompt: str = "ping") -> dict[str, Any]:
        if image_bytes is not None:
            raise ProviderRequestError("Cohere این نسخه از سامانه را برای VLM تصویر پشتیبانی نمی‌کند.")
        return self._post_json(
            f"{self.credential.base_url.rstrip('/')}/chat",
            headers=self._headers(),
            payload={"model": self.credential.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 5},
        )

    def _test_request(self) -> list[str] | None:
        self._chat()
        return None

    def vlm_extract(self, image_bytes: bytes, prompt: str) -> str:
        raise ProviderRequestError("مدل انتخاب‌شده Cohere قابلیت VLM تصویر ندارد.")


def build_adapter(credential: ProviderCredential, client_factory: ClientFactory | None = None) -> AIAdapter:
    if credential.api_type == "gemini":
        return GeminiAdapter(credential, client_factory)
    if credential.api_type == "anthropic":
        return AnthropicAdapter(credential, client_factory)
    if credential.api_type == "azure":
        return AzureOpenAIAdapter(credential, client_factory)
    if credential.api_type == "ollama":
        return OllamaAdapter(credential, client_factory)
    if credential.provider_id == "cohere" or credential.api_type == "custom" and credential.provider_id == "cohere":
        return CohereAdapter(credential, client_factory)
    return OpenAICompatibleAdapter(credential, client_factory)


def _latency(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _response_text(response: Any) -> str:
    return str(getattr(response, "text", "") or "")[:1000]


def _human_http_error(status: int, body: str, secret: str = "") -> str:
    snippet = " ".join(body.strip().split())
    if secret:
        snippet = snippet.replace(secret, "[REDACTED]")
    snippet = snippet[:240]
    if status in {401, 403}:
        return f"کلید API نامعتبر است یا دسترسی کافی نیست (HTTP {status})."
    if status == 404:
        return f"مدل یا آدرس سرویس پیدا نشد (HTTP 404). base URL و نام مدل را بررسی کنید. {snippet}".strip()
    if status == 429:
        return f"سهمیه یا نرخ درخواست سرویس تمام شده است (HTTP 429). {snippet}".strip()
    if status == 400:
        return f"پارامترهای اتصال یا نام مدل پذیرفته نشد (HTTP 400). {snippet}".strip()
    return f"سرویس پاسخ ناموفق داد (HTTP {status}). {snippet}".strip()


def _redact_error(value: str, secret: str = "") -> str:
    # The key is never in a URL, but provider bodies occasionally echo headers or
    # authorization text. Keep raw diagnostics useful without persisting secrets.
    redacted = value.replace("Bearer ", "Bearer [REDACTED]")
    if secret:
        redacted = redacted.replace(secret, "[REDACTED]")
    return " ".join(redacted.split())[:1000]
