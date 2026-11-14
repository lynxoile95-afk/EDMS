"""Load the shared, data-driven provider catalog."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parents[3] / "shared" / "ai-providers.json"


@lru_cache(maxsize=1)
def provider_catalog() -> list[dict[str, Any]]:
    with CATALOG_PATH.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, list):
        raise RuntimeError("shared/ai-providers.json must contain a list")
    return data


def get_provider(provider_id: str) -> dict[str, Any] | None:
    wanted = provider_id.strip().lower()
    return next((item for item in provider_catalog() if item["id"] == wanted), None)


def public_provider_catalog() -> list[dict[str, Any]]:
    """Return catalog data safe for the browser (there are no credentials here)."""
    return provider_catalog()
