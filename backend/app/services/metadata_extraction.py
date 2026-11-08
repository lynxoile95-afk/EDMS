"""Gemini metadata extraction for the transmittal registration screen.

One PDF in, one flat JSON out. The prompt is deliberately narrow: read the title
block of a page, answer with JSON, never guess. Everything the model says is then
re-normalised with the same deterministic helpers the rest of the system uses
(digits, dashes, revision tokens, dates), and validated by code — the model is an
input device, not an authority.

Transport notes that matter:

* ``responseMimeType: application/json`` + ``temperature: 0`` — the spec asks for
  JSON mode, and a deterministic answer is the only kind worth auditing.
* Only the requested pages are rendered and sent (page 1, then page 2 if page 1
  had no title block), which keeps the cost and the error surface small.
* A failed call or unparsable JSON is retried (``registration.extraction_retries``,
  default 2). After that the caller falls back to manual entry — the row still
  exists and is editable, nothing is lost.
* The key stays on the server; it travels in ``x-goog-api-key``, never in a URL.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.core.config import settings
from app.services.normalization import normalize_rev, to_ascii_digits

logger = logging.getLogger(__name__)

GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"

#: The system prompt, kept verbatim so it can be audited against the request spec.
EXTRACTION_PROMPT = """You are a metadata extraction system for engineering documents (oil, gas, petrochemical). Read the title block / header of the first page. Return ONLY valid JSON, no markdown, with keys:
doc_number, title, revision, revision_date (YYYY-MM-DD), purpose_of_issue, project_code, unit, phase, discipline, doc_type, serial_no, contract_no, total_pages, confidence (object: 0 to 1 per field), conflicts (array of strings).
Rules:
1. doc_number: build it from the header coding cells PROJECT CODE, UNIT, PHASE, DISC., DOC. TYPE, SER. NO. joined with '-'. If a full document number is also printed elsewhere, compare them; if different, use the header-built one and add a note to conflicts.
2. title: take from 'DOCUMENT TITLE' in the header. Fix unbalanced parentheses and OCR hyphen losses only when obvious.
3. revision: take from the REV. NO cell of the header only. Never take it from drawing body text or from the revision history table.
4. revision_date: take from the DATE cell of the header. Cross-check with the row of the same revision in the revision history table; if they differ, keep the header value and add to conflicts. Revision table rows may be in random order, so never use 'first row' or 'last row' as the latest revision.
5. purpose_of_issue: the POI value (IFA, IFC, IFR, AFC, ...).
6. Convert Persian/Arabic digits to English digits. Convert dates with dots or slashes to YYYY-MM-DD. If the date is Jalali (year 13xx or 14xx), also return revision_date_jalali and convert to Gregorian.
7. If a field is not visible, return null. Never guess or invent values.
8. Contract numbers may mix Persian and English text; return them as printed."""

#: Keys the answer must contain (missing ones are treated as null).
ANSWER_KEYS = (
    "doc_number",
    "title",
    "revision",
    "revision_date",
    "revision_date_jalali",
    "purpose_of_issue",
    "project_code",
    "unit",
    "phase",
    "discipline",
    "doc_type",
    "serial_no",
    "contract_no",
    "total_pages",
)


@dataclass(slots=True)
class ExtractionOutcome:
    ok: bool = False
    #: normalised, ready for the form
    data: dict = field(default_factory=dict)
    #: the model's JSON exactly as parsed — stored for audit, never mutated
    raw: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    page_used: int | None = None
    attempts: int = 0
    model: str | None = None
    error: str | None = None


def _base_url() -> str:
    url = (settings.vlm_base_url or "").strip().rstrip("/")
    if not url or "/chat/completions" in url:
        return GEMINI_DEFAULT_BASE_URL
    return url


def _model() -> str:
    model = (settings.vlm_model or "").strip()
    # the VLM default is an on-premise chat model; a Gemini endpoint needs a Gemini name
    if not model or "/" in model:
        return GEMINI_DEFAULT_MODEL
    return model


def _endpoint_trusted() -> bool:
    """Reuse the one privacy guard: local, or remote explicitly confirmed."""
    from app.extraction.vlm import VLMExtractor

    return VLMExtractor().endpoint_trusted()


def _render_pages(pdf_path: str, pages: list[int]) -> list[tuple[int, bytes]]:
    """Render the requested pages to PNG, skipping ones the PDF does not have."""
    from app.services import pdf_utils

    rendered: list[tuple[int, bytes]] = []
    try:
        info = pdf_utils.inspect_pdf(pdf_path)
        total = info.page_count
    except Exception:  # noqa: BLE001 - unreadable PDF is handled by the caller
        return rendered
    for page_no in pages:
        if page_no < 1 or page_no > total:
            continue
        try:
            rendered.append((page_no, pdf_utils.render_png(pdf_path, page_no, dpi=170)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("render page %s failed: %s", page_no, exc)
    return rendered


def _call_gemini(image_png: bytes, timeout: int) -> dict:
    """One request. Raises on transport/HTTP errors so the caller can retry."""
    import base64

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": EXTRACTION_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(image_png).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "maxOutputTokens": 1200,
        },
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{_base_url()}/models/{_model()}:generateContent",
            headers={"x-goog-api-key": settings.vlm_api_key},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    try:
        parts = body["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as exc:
        raise ValueError("پاسخ سرویس هوش مصنوعی ساختار مورد انتظار را نداشت.") from exc
    text = "\n".join(part.get("text", "") for part in parts)
    if not text.strip():
        raise ValueError("سرویس هوش مصنوعی پاسخ خالی برگرداند.")
    return parse_json_answer(text)


def parse_json_answer(text: str) -> dict:
    """Parse the model's JSON, tolerating a stray code fence or prose around it."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("پاسخ مدل JSON معتبر نبود.")
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("پاسخ مدل یک شیء JSON نبود.")
    return data


# --------------------------------------------------------------------- normalising
def _clean(value) -> str | None:
    if value is None:
        return None
    text = to_ascii_digits(str(value)).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    return text


def normalise_answer(raw: dict) -> dict:
    """Model JSON -> the flat fields the form and the validators use."""
    data: dict = {}
    for key in ANSWER_KEYS:
        value = raw.get(key)
        if key == "total_pages":
            text = _clean(value)
            data[key] = int(text) if text and text.isdigit() else None
        elif key == "doc_number":
            text = _clean(value)
            data[key] = text.upper().replace(" ", "") if text else None
        elif key == "revision":
            data[key] = normalize_rev(_clean(value)) or None
        elif key in {"revision_date", "revision_date_jalali"}:
            data[key] = normalise_date(_clean(value)) or _clean(value)
        else:
            text = _clean(value)
            data[key] = text.upper() if key in {"purpose_of_issue", "phase", "discipline", "doc_type"} and text else text

    confidence = raw.get("confidence") or {}
    cleaned_confidence: dict[str, float] = {}
    if isinstance(confidence, dict):
        for key, value in confidence.items():
            try:
                cleaned_confidence[str(key)] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
    conflicts = raw.get("conflicts") or []
    if isinstance(conflicts, str):
        conflicts = [conflicts]
    cleaned_conflicts = [str(item).strip() for item in conflicts if str(item).strip()]

    data["revision_date_gregorian"] = to_gregorian(
        data.get("revision_date"), data.get("revision_date_jalali")
    )
    return {"data": data, "confidence": cleaned_confidence, "conflicts": cleaned_conflicts}


def normalise_date(value: str | None) -> str | None:
    """``1402.09.06`` / ``1402/09/06`` / ``2023-11-27`` -> ``YYYY-MM-DD`` (as printed)."""
    if not value:
        return None
    digits = to_ascii_digits(value)
    parts = re.findall(r"\d+", digits)
    if len(parts) < 3:
        return value.strip()
    year, month, day = parts[0], parts[1].zfill(2), parts[2].zfill(2)
    return f"{year}-{month}-{day}"


def to_gregorian(*candidates: str | None) -> str | None:
    """Convert a Jalali date (13xx/14xx) to Gregorian; pass a Gregorian through."""
    from app.utils.jalali import jalali_to_gregorian

    for candidate in candidates:
        if not candidate:
            continue
        parts = re.findall(r"\d+", to_ascii_digits(candidate))
        if len(parts) < 3:
            continue
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        if 1300 <= year <= 1499:
            try:
                return jalali_to_gregorian(year, month, day).isoformat()
            except (ValueError, OverflowError):
                continue
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------------- entry
def extract_metadata(
    pdf_path: str,
    *,
    pages: list[int] | None = None,
    retries: int | None = None,
    timeout: int | None = None,
) -> ExtractionOutcome:
    """Read one PDF's title block through Gemini.

    Never raises for ordinary failures: the caller shows ``outcome.error`` and lets
    the operator type the row by hand.
    """
    pages = pages or [1, 2]
    retries = 2 if retries is None else retries
    timeout = timeout or settings.vlm_timeout_seconds

    if not settings.vlm_api_key or settings.vlm_api_key == "EMPTY":
        return ExtractionOutcome(error="کلید هوش مصنوعی تنظیم نشده است؛ ردیف دستی ثبت شد.")
    if not _endpoint_trusted():
        return ExtractionOutcome(
            error="آدرس سرویس هوش مصنوعی داخلی نیست و ارسال به بیرون تأیید نشده است؛ ردیف دستی ثبت شد."
        )

    rendered = _render_pages(pdf_path, pages)
    if not rendered:
        return ExtractionOutcome(error="صفحه‌ای برای خواندن پیدا نشد (فایل PDF معتبر نیست).")

    last_error: str | None = None
    attempts = 0
    for page_no, image in rendered:
        for _attempt in range(retries + 1):
            attempts += 1
            try:
                raw = _call_gemini(image, timeout)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                last_error = _explain_status(status)
                if status in {401, 403, 404}:
                    # a wrong key or model will not fix itself by retrying
                    return ExtractionOutcome(
                        attempts=attempts, model=_model(), error=last_error, page_used=page_no
                    )
                continue
            except httpx.HTTPError as exc:
                last_error = f"ارتباط با سرویس هوش مصنوعی برقرار نشد: {str(exc)[:160]}"
                continue
            except ValueError as exc:
                last_error = str(exc)
                continue

            normalised = normalise_answer(raw)
            # "no title block on this page" -> try the next page instead of guessing
            if not any(
                normalised["data"].get(key)
                for key in ("doc_number", "title", "revision", "purpose_of_issue")
            ):
                last_error = f"در صفحه {page_no} کادر مشخصات شناسایی نشد."
                break
            return ExtractionOutcome(
                ok=True,
                data=normalised["data"],
                raw=raw,
                confidence=normalised["confidence"],
                conflicts=normalised["conflicts"],
                page_used=page_no,
                attempts=attempts,
                model=_model(),
            )

    return ExtractionOutcome(attempts=attempts, model=_model(), error=last_error, page_used=None)


def _explain_status(status: int) -> str:
    if status in {401, 403}:
        return f"کلید هوش مصنوعی پذیرفته نشد (HTTP {status})."
    if status == 404:
        return f"آدرس یا نام مدل پیدا نشد (HTTP {status})."
    if status == 429:
        return "سهمیه یا نرخ درخواست سرویس هوش مصنوعی تمام شده است (HTTP 429)."
    return f"سرویس هوش مصنوعی پاسخ ناموفق داد (HTTP {status})."
