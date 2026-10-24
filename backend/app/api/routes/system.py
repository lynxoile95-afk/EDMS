"""Extraction capabilities the UI can show without exposing any secret.

The question the upload screen has to answer for the operator is simple: *does this
deployment need an AI key to ingest drawings, and is one configured?* The answer never
includes the key itself — only whether one is set.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.extraction.vlm import VLMExtractor

router = APIRouter(tags=["system"])

#: What the operator must be told on the upload screen.
EXTRACTION_NOTE_FA = (
    "استخراج با قواعد و لایه متن PDF انجام می‌شود و با OCR فارسی/انگلیسی کامل می‌شود؛ "
    "هیچ کلید هوش مصنوعی برای کار کردن بارگذاری لازم نیست. کلید فقط برای «لایه کمکی VLM» "
    "است که تنها در موارد مبهم و در صورت فعال شدن به کار می‌رود."
)


@router.get("/system/capabilities", summary="توانمندی‌های استخراج (بدون افشای کلید)")
def capabilities(user: CurrentUser, db: DbSession) -> dict:
    vlm = VLMExtractor()
    return {
        "ocr_enabled": settings.ocr_enabled,
        "ocr_engine": settings.ocr_engine,
        "ocr_langs": settings.ocr_langs,
        "ai_required_for_upload": False,
        "vlm_enabled": settings.vlm_enabled,
        "vlm_api_key_set": bool(settings.vlm_api_key and settings.vlm_api_key != "EMPTY"),
        "vlm_model": settings.vlm_model,
        "vlm_base_url": settings.vlm_base_url,
        "vlm_endpoint_allowed": vlm.is_local_endpoint(),
        "note_fa": EXTRACTION_NOTE_FA,
    }
