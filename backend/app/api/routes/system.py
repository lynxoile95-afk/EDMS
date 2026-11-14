"""Extraction capabilities the UI can show without exposing any secret.

The question the upload screen has to answer for the operator is simple: *does this
deployment need an AI key to ingest drawings, and is one configured?* The answer never
includes the key itself — only whether one is set.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.ai.service import active_runtime
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
    # The upload strip must reflect the same persisted active Credential that the
    # extraction worker will use after a reload, with the old VLM_* environment
    # settings still serving as the fallback when no Credential exists.
    runtime = active_runtime(db)
    vlm = VLMExtractor()
    return {
        "ocr_enabled": settings.ocr_enabled,
        "ocr_engine": settings.ocr_engine,
        "ocr_langs": settings.ocr_langs,
        "ai_required_for_upload": False,
        "vlm_enabled": runtime.enabled,
        "vlm_provider": runtime.provider_id,
        "vlm_api_key_set": bool(runtime.api_key),
        "vlm_model": runtime.model,
        "vlm_base_url": runtime.base_url,
        "vlm_endpoint_allowed": vlm.endpoint_trusted(),
        "vlm_remote_allowed": runtime.remote_consent,
        "note_fa": EXTRACTION_NOTE_FA,
    }
