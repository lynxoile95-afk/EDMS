"""Small AES-256-GCM envelope for provider API keys.

Keys never enter logs, URLs or API responses. The configured secret is hashed to a
32-byte AES key so operators may supply a normal high-entropy environment secret
instead of having to format raw binary material.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_AAD = b"edms-ai-credential-v1"


def _key() -> bytes:
    configured = (settings.credentials_encryption_key or "").strip()
    # Keeping SECRET_KEY as a development fallback makes the migration safe for
    # existing .env files; production validation warns when the dedicated secret
    # is missing, while the encryption primitive remains AES-256-GCM.
    material = configured or settings.secret_key
    return hashlib.sha256(material.encode("utf-8")).digest()


def encrypt_secret(value: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, value.encode("utf-8"), _AAD)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    if len(raw) <= 12:
        raise ValueError("encrypted credential payload is invalid")
    plaintext = AESGCM(_key()).decrypt(raw[:12], raw[12:], _AAD)
    return plaintext.decode("utf-8")


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if len(text) <= 8:
        return "•" * len(text)
    return f"{text[:4]}••••••••{text[-4:]}"
