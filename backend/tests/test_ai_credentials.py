"""Persisted provider credentials and adapter connection tests."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from fastapi.testclient import TestClient

from app.ai.adapters import ProviderCredential, build_adapter
from app.ai.registry import provider_catalog
from app.ai.service import ensure_legacy_migration
from app.core.config import settings
from app.models import AICredential
from tests.conftest import auth_headers


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self.payload


class FakeClient:
    response = FakeResponse({"data": [{"id": "gpt-4o-mini"}]})
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers or {}})
        return self.response

    def post(self, url, headers=None, json=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers or {}, "json": json or {}})
        return self.response


def test_shared_registry_contains_all_requested_providers():
    ids = {provider["id"] for provider in provider_catalog()}
    assert {
        "openai", "gemini", "anthropic", "azure-openai", "mistral", "groq", "deepseek",
        "xai", "openrouter", "together", "perplexity", "cohere", "huggingface",
        "ollama", "lm-studio", "custom-openai",
    } <= ids


def test_legacy_gemini_settings_migrate_to_an_active_encrypted_credential(db, monkeypatch):
    monkeypatch.setattr(settings, "vlm_provider", "gemini")
    monkeypatch.setattr(settings, "vlm_base_url", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setattr(settings, "vlm_model", "Qwen/Qwen2.5-VL-7B-Instruct")
    monkeypatch.setattr(settings, "vlm_api_key", "legacy-gemini-secret")
    monkeypatch.setattr(settings, "vlm_enabled", True)
    monkeypatch.setattr(settings, "vlm_allow_remote", True)

    prefs = ensure_legacy_migration(db)
    row = db.get(AICredential, prefs.active_credential_id)
    assert row is not None
    assert row.provider_id == "gemini"
    assert row.model == "gemini-2.0-flash"
    assert row.is_active is True
    assert row.api_key_encrypted != "legacy-gemini-secret"


def test_openai_adapter_connection_is_server_side_and_masks_no_key(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr("app.ai.adapters.httpx.Client", FakeClient)
    adapter = build_adapter(
        ProviderCredential(
            provider_id="openai",
            api_type="openai-compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="secret-key",
        )
    )
    result = adapter.test_connection()
    assert result.ok is True
    assert FakeClient.calls[0]["url"].endswith("/models")
    assert FakeClient.calls[0]["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in json.dumps(asdict(result), ensure_ascii=False)


def test_wrong_model_is_reported_as_a_safe_404(monkeypatch):
    FakeClient.response = FakeResponse({"data": [{"id": "gpt-4o-mini"}]})
    monkeypatch.setattr("app.ai.adapters.httpx.Client", FakeClient)
    result = build_adapter(
        ProviderCredential(
            provider_id="openai",
            api_type="openai-compatible",
            base_url="https://api.openai.com/v1",
            model="not-a-real-model",
            api_key="secret-key",
        )
    ).test_connection()
    assert result.ok is False
    assert result.status == 404
    assert "مدل" in result.message


def test_credentials_are_encrypted_masked_and_testable(client: TestClient, users, db, monkeypatch):
    admin = auth_headers(client, "admin@edms-test.com")
    saved = client.post(
        "/api/v1/ai/credentials",
        headers=admin,
        json={
            "provider": "gemini",
            "name": "Gemini – main",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "api_key": "AIza-secret-value",
            "model": "gemini-2.0-flash",
            "remote_consent": True,
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["keyMasked"].startswith("AIza")
    assert "AIza-secret-value" not in saved.text
    row = db.get(AICredential, uuid.UUID(body["id"]))
    assert row is not None
    assert row.api_key_encrypted != "AIza-secret-value"
    assert row.api_key_encrypted

    listed = client.get("/api/v1/ai/credentials", headers=admin)
    assert listed.status_code == 200
    assert "AIza-secret-value" not in listed.text
    assert any(item["id"] == body["id"] for item in listed.json())

    active = client.put(
        "/api/v1/ai/active",
        headers=admin,
        json={"credential_id": body["id"]},
    )
    assert active.status_code == 200, active.text

    FakeClient.calls = []
    FakeClient.response = FakeResponse({"models": [{"name": "models/gemini-2.0-flash"}]})
    monkeypatch.setattr("app.ai.adapters.httpx.Client", FakeClient)
    tested = client.post(f"/api/v1/ai/credentials/{body['id']}/test", headers=admin)
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True
    assert tested.json()["latencyMs"] is not None


def test_capabilities_follow_the_persisted_active_provider(client: TestClient, users):
    admin = auth_headers(client, "admin@edms-test.com")
    saved = client.post(
        "/api/v1/ai/credentials",
        headers=admin,
        json={
            "provider": "ollama",
            "name": "Local active",
            "base_url": "http://localhost:11434",
            "model": "llama3.2-vision",
            "is_active": True,
        },
    )
    assert saved.status_code == 200, saved.text
    capabilities = client.get("/api/v1/system/capabilities", headers=admin)
    assert capabilities.status_code == 200, capabilities.text
    payload = capabilities.json()
    assert payload["vlm_provider"] == "ollama"
    assert payload["vlm_model"] == "llama3.2-vision"
    assert payload["vlm_api_key_set"] is False


def test_cloud_active_requires_consent_but_local_provider_does_not(client: TestClient, users):
    admin = auth_headers(client, "admin@edms-test.com")
    cloud = client.post(
        "/api/v1/ai/credentials",
        headers=admin,
        json={
            "provider": "openai",
            "name": "No consent",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-cloud",
            "model": "gpt-4o-mini",
        },
    )
    assert cloud.status_code == 200, cloud.text
    denied = client.put("/api/v1/ai/active", headers=admin, json={"credential_id": cloud.json()["id"]})
    assert denied.status_code == 400
    assert "تأیید" in denied.json()["detail"]

    local = client.post(
        "/api/v1/ai/credentials",
        headers=admin,
        json={
            "provider": "ollama",
            "name": "Local Ollama",
            "base_url": "http://localhost:11434",
            "model": "llama3.2-vision",
        },
    )
    assert local.status_code == 200, local.text
    allowed = client.put("/api/v1/ai/active", headers=admin, json={"credential_id": local.json()["id"]})
    assert allowed.status_code == 200, allowed.text


def test_upsert_preserves_an_existing_key_when_editing(client: TestClient, users, db):
    admin = auth_headers(client, "admin@edms-test.com")
    first = client.post(
        "/api/v1/ai/credentials",
        headers=admin,
        json={
            "provider": "openai",
            "name": "OpenAI main",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-preserve-me",
            "model": "gpt-4o-mini",
            "remote_consent": True,
        },
    )
    assert first.status_code == 200, first.text
    credential_id = first.json()["id"]
    edited = client.post(
        "/api/v1/ai/credentials",
        headers=admin,
        json={
            "provider": "openai",
            "name": "OpenAI main",
            "base_url": "https://api.openai.com/v1/",
            "model": "gpt-4.1-mini",
            "remote_consent": True,
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["id"] == credential_id
    assert edited.json()["keySet"] is True
    assert db.query(AICredential).count() == 1


def test_unsaved_test_returns_safe_failure_reason(client: TestClient, users, monkeypatch):
    admin = auth_headers(client, "admin@edms-test.com")
    FakeClient.response = FakeResponse({"error": {"message": "invalid api key wrong-secret"}}, status_code=401)
    monkeypatch.setattr("app.ai.adapters.httpx.Client", FakeClient)
    response = client.post(
        "/api/v1/ai/test",
        headers=admin,
        json={
            "provider": "openai",
            "name": "temporary",
            "base_url": "https://api.openai.com/v1",
            "api_key": "wrong-secret",
            "model": "gpt-4o-mini",
            "remote_consent": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["status"] == 401
    assert "نامعتبر" in payload["message"]
    assert "wrong-secret" not in response.text
