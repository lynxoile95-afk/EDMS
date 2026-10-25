"""The optional AI layer: two providers, one privacy guard, no key ever returned.

The VLM is the only place this system can talk to an outside service, so the tests
pin down three things:

* both request shapes are exactly what the provider expects (OpenAI-compatible
  ``/chat/completions`` and Gemini ``:generateContent``),
* a public endpoint stays refused until an administrator explicitly confirms the
  egress, and
* the admin endpoints never hand the key back, not even masked.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.extraction.vlm import VLMExtractor
from tests.conftest import auth_headers


@pytest.fixture()
def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 240), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def vlm_settings():
    """Snapshot every VLM setting and put it back, so tests cannot leak into each other."""
    names = (
        "vlm_enabled",
        "vlm_provider",
        "vlm_base_url",
        "vlm_model",
        "vlm_api_key",
        "vlm_allow_remote",
    )
    before = {name: getattr(settings, name) for name in names}
    yield settings
    for name, value in before.items():
        setattr(settings, name, value)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("failed", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Records the outgoing request and answers with a canned provider response."""

    calls: list[dict] = []
    response: _FakeResponse = _FakeResponse({})

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def post(self, url: str, headers: dict | None = None, json: dict | None = None):
        type(self).calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return type(self).response

    def get(self, url: str, headers: dict | None = None):
        type(self).calls.append({"url": url, "headers": headers or {}, "json": {}})
        return type(self).response


GEMINI_ANSWER = {
    "fields": {
        "doc_code": "۲۱۰۲-۲۰-ED-ME-DWG-4501",
        "rev": "rev A0",
        "title": "Tank orientation",
        "discipline": "MECHANICAL",
        "unit": "20",
    },
    "confidence": {"doc_code": 0.99, "rev": 0.4},  # capped on the way out
    "notes": None,
}


# --------------------------------------------------------------- request shapes
def test_gemini_call_uses_generate_content_and_the_header_key(
    monkeypatch, png, vlm_settings
):
    vlm_settings.vlm_enabled = True
    vlm_settings.vlm_provider = "gemini"
    vlm_settings.vlm_base_url = "https://generativelanguage.googleapis.com/v1beta"
    vlm_settings.vlm_model = "gemini-2.0-flash"
    vlm_settings.vlm_api_key = "secret-key"
    vlm_settings.vlm_allow_remote = True

    _FakeClient.calls = []
    _FakeClient.response = _FakeResponse({"candidates": [{"content": {"parts": [{"text": json.dumps(GEMINI_ANSWER)}]}}]})
    monkeypatch.setattr("app.extraction.vlm.httpx.Client", _FakeClient)

    result = VLMExtractor().extract(png)

    call = _FakeClient.calls[0]
    assert call["url"].endswith("/models/gemini-2.0-flash:generateContent")
    assert call["headers"]["x-goog-api-key"] == "secret-key"
    assert "key" not in json.dumps(call["url"]).lower()  # never in the URL
    part = call["json"]["contents"][0]["parts"]
    assert any("inline_data" in item for item in part)
    image_part = next(item for item in part if "inline_data" in item)["inline_data"]
    assert image_part["mime_type"] == "image/png"
    assert call["json"]["generationConfig"]["responseMimeType"] == "application/json"

    # the model's answer is re-normalised with our own helpers, not trusted raw
    assert result["doc_code"][0] == "2102-20-ED-ME-DWG-4501"
    assert result["rev"][0] == "A0"
    assert result["doc_code"][1] <= 0.85  # never outranks a clean text layer


def test_openai_call_shape_is_unchanged(monkeypatch, png, vlm_settings):
    vlm_settings.vlm_enabled = True
    vlm_settings.vlm_provider = "openai"
    vlm_settings.vlm_base_url = "http://localhost:8000/v1"
    vlm_settings.vlm_model = "Qwen/Qwen2.5-VL-7B-Instruct"
    vlm_settings.vlm_api_key = "secret-key"
    vlm_settings.vlm_allow_remote = False

    _FakeClient.calls = []
    answer = {"fields": {"doc_code": "2102-20-ED-ME-DWG-4501"}}
    _FakeClient.response = _FakeResponse(
        {"choices": [{"message": {"content": json.dumps(answer)}}]}
    )
    monkeypatch.setattr("app.extraction.vlm.httpx.Client", _FakeClient)

    VLMExtractor().extract(png)

    call = _FakeClient.calls[0]
    assert call["url"] == "http://localhost:8000/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret-key"
    assert call["json"]["model"] == "Qwen/Qwen2.5-VL-7B-Instruct"
    assert call["json"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_public_endpoint_is_refused_until_remote_is_confirmed(monkeypatch, png, vlm_settings):
    vlm_settings.vlm_enabled = True
    vlm_settings.vlm_provider = "gemini"
    vlm_settings.vlm_base_url = "https://generativelanguage.googleapis.com/v1beta"
    vlm_settings.vlm_api_key = "secret-key"
    vlm_settings.vlm_allow_remote = False

    _FakeClient.calls = []
    monkeypatch.setattr("app.extraction.vlm.httpx.Client", _FakeClient)

    extractor = VLMExtractor()
    assert extractor.is_available() is False
    assert extractor.extract(png) == {}
    assert _FakeClient.calls == []  # nothing was even attempted

    vlm_settings.vlm_allow_remote = True
    assert VLMExtractor().is_available() is True


# --------------------------------------------------------------- admin endpoints
def test_ai_config_round_trip_never_returns_the_key(client: TestClient, users, vlm_settings):
    admin = auth_headers(client, "admin@edms-test.com")
    saved = client.put(
        "/api/v1/admin/ai-config",
        headers=admin,
        json={
            "provider": "gemini",
            "base_url": "https://generativelanguage.googleapis.com",
            "model": "gemini-2.0-flash",
            "api_key": "AIza-very-secret",
            "allow_remote": True,
            "confirm_remote_acknowledged": True,
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["provider"] == "gemini"
    assert body["base_url"] == "https://generativelanguage.googleapis.com/v1beta"
    assert body["key_set"] is True
    assert body["endpoint_trusted"] is True
    assert "AIza-very-secret" not in saved.text

    fetched = client.get("/api/v1/admin/ai-config", headers=admin)
    assert "AIza-very-secret" not in fetched.text
    assert fetched.json()["key_set"] is True

    # locking it back down needs no acknowledgement
    locked = client.put("/api/v1/admin/ai-config", headers=admin, json={"allow_remote": False})
    assert locked.status_code == 200
    assert locked.json()["endpoint_trusted"] is False


def test_a_public_endpoint_needs_an_explicit_acknowledgement(client: TestClient, users, vlm_settings):
    admin = auth_headers(client, "admin@edms-test.com")
    refused = client.put(
        "/api/v1/admin/ai-config",
        headers=admin,
        json={"provider": "gemini", "base_url": "https://generativelanguage.googleapis.com"},
    )
    assert refused.status_code == 400
    assert "تأیید" in refused.json()["detail"]

    switched_on = client.put(
        "/api/v1/admin/ai-config",
        headers=admin,
        json={"allow_remote": True},  # no ack in the same request
    )
    assert switched_on.status_code == 400


def test_ai_config_is_admin_only(client: TestClient, users, vlm_settings):
    dcc = auth_headers(client, "dcc@edms-test.com")
    assert client.get("/api/v1/admin/ai-config", headers=dcc).status_code == 403
    assert client.put("/api/v1/admin/ai-config", headers=dcc, json={"api_key": "x"}).status_code == 403
    assert client.post("/api/v1/admin/ai-config/test", headers=dcc).status_code == 403


def test_the_probe_explains_why_it_failed(client: TestClient, users, vlm_settings):
    admin = auth_headers(client, "admin@edms-test.com")

    # disabled: say so instead of an opaque failure
    vlm_settings.vlm_enabled = False
    probe = client.post("/api/v1/admin/ai-config/test", headers=admin).json()
    assert probe["ok"] is False
    assert "فعال نشده" in probe["error"]

    # enabled but pointed at an unreachable local port: the reason must be transport
    vlm_settings.vlm_enabled = True
    vlm_settings.vlm_provider = "openai"
    vlm_settings.vlm_base_url = "http://127.0.0.1:9/v1"
    vlm_settings.vlm_api_key = "whatever"
    vlm_settings.vlm_timeout_seconds = 2
    probe = client.post("/api/v1/admin/ai-config/test", headers=admin).json()
    assert probe["ok"] is False
    assert probe["error"]
    assert "whatever" not in json.dumps(probe)
