"""Malware scan hook (ClamAV over clamd's INSTREAM protocol).

Enabled with ``AV_ENABLED=true``.  When disabled the scan reports ``SKIPPED`` —
never ``CLEAN`` — so the audit trail is honest.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from app.core.config import settings
from app.models.enums import AVStatus


@dataclass(slots=True)
class ScanResult:
    status: AVStatus
    signature: str | None = None
    detail: str | None = None


def scan_bytes(data: bytes) -> ScanResult:
    if not settings.av_enabled:
        return ScanResult(status=AVStatus.SKIPPED, detail="AV disabled by configuration")

    try:
        with socket.create_connection((settings.clamav_host, settings.clamav_port), timeout=30) as sock:
            sock.sendall(b"zINSTREAM\0")
            chunk_size = 8192
            for offset in range(0, len(data), chunk_size):
                chunk = data[offset : offset + chunk_size]
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))
            response = b""
            while True:
                brick = sock.recv(4096)
                if not brick:
                    break
                response += brick
    except OSError as exc:
        # Fail closed: an unreachable scanner must not silently pass files.
        return ScanResult(status=AVStatus.PENDING, detail=f"clamd unreachable: {exc}")

    text = response.decode("utf-8", errors="replace").strip()
    if text.endswith("OK"):
        return ScanResult(status=AVStatus.CLEAN, detail=text)
    if "FOUND" in text:
        signature = text.split(":", 1)[-1].replace("FOUND", "").strip()
        return ScanResult(status=AVStatus.INFECTED, signature=signature, detail=text)
    return ScanResult(status=AVStatus.PENDING, detail=text or "unknown clamd response")
