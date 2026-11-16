"""Object storage abstraction (MinIO/S3 in production, filesystem in tests)."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import settings


class StorageError(RuntimeError):
    pass


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def put_file(self, key: str, path: str, content_type: str = "application/octet-stream") -> str: ...
    def get_bytes(self, key: str) -> bytes: ...
    def download_to(self, key: str, target_path: str) -> str: ...
    def presigned_url(self, key: str, expires_seconds: int | None = None, filename: str | None = None) -> str: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.client import Config
        from botocore.exceptions import ClientError

        self._client_error = ClientError
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )
        # separate client for signing URLs so browsers get a reachable hostname
        self._signer = boto3.client(
            "s3",
            endpoint_url=settings.s3_public_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except self._client_error:
            self.client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        import io

        self.client.upload_fileobj(io.BytesIO(data), self.bucket, key, ExtraArgs={"ContentType": content_type})
        return key

    def put_file(self, key: str, path: str, content_type: str = "application/octet-stream") -> str:
        self.client.upload_file(path, self.bucket, key, ExtraArgs={"ContentType": content_type})
        return key

    def get_bytes(self, key: str) -> bytes:
        import io

        buffer = io.BytesIO()
        self.client.download_fileobj(self.bucket, key, buffer)
        return buffer.getvalue()

    def download_to(self, key: str, target_path: str) -> str:
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, target_path)
        return target_path

    def presigned_url(
        self, key: str, expires_seconds: int | None = None, filename: str | None = None
    ) -> str:
        params: dict = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        url = self._signer.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_seconds or settings.download_link_ttl_seconds,
        )
        # swap internal endpoint for the browser-reachable one
        if settings.s3_endpoint != settings.s3_public_endpoint:
            url = url.replace(settings.s3_endpoint, settings.s3_public_endpoint, 1)
        return url

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self._client_error:
            return False


class LocalStorage:
    """Filesystem backend — used by the test suite and offline demos."""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or os.environ.get("LOCAL_STORAGE_PATH", tempfile.gettempdir() + "/edms-storage"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("..", "_").lstrip("/")
        return self.root / safe

    def ensure_bucket(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def put_file(self, key: str, path: str, content_type: str = "application/octet-stream") -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def download_to(self, key: str, target_path: str) -> str:
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), target_path)
        return target_path

    def presigned_url(self, key: str, expires_seconds: int | None = None, filename: str | None = None) -> str:
        # local dev: served by an authenticated API route instead of a signed URL
        return f"{settings.api_v1_prefix}/files/download/{key}"

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage() if settings.storage_backend == "s3" else LocalStorage()
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Test hook."""
    global _storage
    _storage = storage


def sha256_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def sha256_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def build_key(*parts: str) -> str:
    return "/".join(str(p).strip("/") for p in parts if p not in (None, ""))
