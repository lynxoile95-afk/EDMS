"""The layered extraction pipeline.

Order (cheapest first — each layer only fills what is still missing):

    1. native text layer with coordinates          (PyMuPDF words)
       + template ROI when the project has a TitleBlockTemplate
       + label heuristics otherwise
    2. whole-page pattern search for the doc code / revision
    3. OCR of the title-block crop (scanned drawings)
    4. VLM fallback for still-missing or low-confidence fields
    5. normalisation + deterministic validation (always, on every value)

The result keeps *all* evidence: per-field source, confidence, bbox, the raw
text, the crop key and the model/pipeline versions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.extraction.base import FieldCandidate, LayerOutput
from app.extraction.ocr import get_ocr_engine
from app.extraction.title_block import (
    group_lines,
    parse_fields_from_words,
    region_words,
    select_template,
)
from app.extraction.vlm import get_vlm
from app.models.enums import ExtractionSource
from app.services import pdf_utils
from app.services.normalization import (
    extract_code_candidates,
    normalize_code,
    normalize_rev,
)
from app.services.project_settings import merge_settings

logger = logging.getLogger(__name__)

#: fields the pipeline tries to fill, in priority order
TARGET_FIELDS = (
    "doc_code",
    "rev",
    "title",
    "discipline",
    "unit",
    "doc_status",
    "date",
    "sheet_no",
    "sheet_count",
)
REQUIRED_FOR_AUTO = ("doc_code", "rev")

#: default ROI used when a project has no template: bottom-right corner
DEFAULT_TITLE_BLOCK_REGION = {"x": 0.55, "y": 0.68, "w": 0.45, "h": 0.32}


@dataclass(slots=True)
class ExtractionSettings:
    doc_code_pattern: str = r"\S+"
    ocr_fallback: bool = True
    vlm_fallback: bool = False
    vlm_max_calls: int = 2
    ocr_dpi: int = 300
    ocr_langs: str = "fas+eng"
    title_block_required: bool = True


@dataclass(slots=True)
class PipelineResult:
    fields: dict[str, FieldCandidate] = field(default_factory=dict)
    methods_used: list[str] = field(default_factory=list)
    raw_text: str = ""
    confidence: dict[str, float] = field(default_factory=dict)
    title_block_found: bool = False
    template_id: str | None = None
    template_name: str | None = None
    title_block_png: bytes | None = None
    models: dict[str, str] = field(default_factory=dict)
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    page_width: float = 0.0
    page_height: float = 0.0
    has_text_layer: bool = True

    def value(self, name: str) -> str | None:
        candidate = self.fields.get(name)
        return candidate.value if candidate else None

    def as_payload(self) -> dict[str, Any]:
        return {
            "fields": {name: c.as_dict() for name, c in self.fields.items()},
            "methods_used": self.methods_used,
            "confidence": self.confidence,
            "title_block_found": self.title_block_found,
            "template_id": self.template_id,
            "template_name": self.template_name,
            "models": self.models,
            "duration_ms": self.duration_ms,
            "warnings": self.warnings,
            "has_text_layer": self.has_text_layer,
            "page": {"width": self.page_width, "height": self.page_height},
        }


class ExtractionPipeline:
    """Stateless: safe to reuse across documents inside a worker process."""

    def __init__(
        self,
        *,
        project_settings: dict | None = None,
        templates: list | None = None,
        ocr_engine=None,
        vlm=None,
    ) -> None:
        merged = merge_settings(project_settings)
        self.settings = ExtractionSettings(
            doc_code_pattern=merged["doc_code_pattern"],
            ocr_fallback=bool(merged["ocr_fallback"]) and settings.ocr_enabled,
            vlm_fallback=bool(merged["vlm_fallback"]),
            vlm_max_calls=int(merged["vlm_max_calls_per_document"]),
            ocr_dpi=settings.ocr_dpi,
            ocr_langs=settings.ocr_langs,
            title_block_required=bool(merged["title_block_required"]),
        )
        self.templates = templates or []
        self.ocr = ocr_engine or get_ocr_engine()
        self.vlm = vlm if vlm is not None else (get_vlm() if self.settings.vlm_fallback else None)
        self.vlm_calls = 0

    # ------------------------------------------------------------------ entry
    def run(self, pdf_path: str, page_no: int = 1) -> PipelineResult:
        started = time.perf_counter()
        result = PipelineResult(models={"pipeline": settings.pipeline_version})

        try:
            info = pdf_utils.inspect_pdf(pdf_path)
            page = next((p for p in info.pages if p.page_no == page_no), None)
            if page is None:
                raise pdf_utils.PdfError(f"صفحه {page_no} در فایل وجود ندارد.")
            result.page_width, result.page_height = page.width_pt, page.height_pt
            result.has_text_layer = page.has_text
        except pdf_utils.PdfError as exc:
            result.error = str(exc)
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            return result

        # ------------------------------------------------ layer 1: text layer
        words = []
        if result.has_text_layer:
            words = pdf_utils.extract_words(pdf_path, page_no)
            full_text = " ".join(w.text for w in words)
            result.raw_text = full_text
            template = select_template(self.templates, page.aspect_ratio, full_text)
            if template is not None:
                result.template_id = str(getattr(template, "id", "")) or None
                result.template_name = getattr(template, "name", None)
                regions = getattr(template, "regions", None) or {}
                region = regions.get("title_block") or regions.get("default") or DEFAULT_TITLE_BLOCK_REGION
                scoped = region_words(words, region, page.width_pt, page.height_pt) or words
                title_block_found = bool(scoped)
            else:
                scoped = region_words(words, DEFAULT_TITLE_BLOCK_REGION, page.width_pt, page.height_pt)
                title_block_found = len(scoped) >= 5
                if not title_block_found:
                    scoped = words  # fall back to whole page

            layer = self._parse_words(scoped, page_no=page_no, template=template, confidence=0.95)
            self._merge(result, layer)

            # ------------------------------------------- layer 2: page search
            self._fill_from_text(result, full_text, page_no)
            if layer.fields:
                result.title_block_found = title_block_found and len(layer.fields) >= 2
        else:
            result.raw_text = ""
            self._append_method(result, "no_text_layer")

        # ---------------------------------------------------- layer 3: OCR
        needs_ocr = (
            self.settings.ocr_fallback
            and (
                not result.has_text_layer
                or any(name not in result.fields for name in REQUIRED_FOR_AUTO)
                or any(result.confidence.get(n, 0) < 0.6 for n in REQUIRED_FOR_AUTO if n in result.fields)
            )
        )
        if needs_ocr:
            ocr_layer = self._run_ocr(pdf_path, page_no, result)
            if ocr_layer is not None:
                self._merge(result, ocr_layer)
                if not result.raw_text:
                    result.raw_text = ocr_layer.text

        # ---------------------------------------------------- layer 4: VLM
        missing_required = [n for n in REQUIRED_FOR_AUTO if n not in result.fields]
        low_conf = [n for n in REQUIRED_FOR_AUTO if result.confidence.get(n, 0) < 0.6]
        if (missing_required or low_conf) and self._vlm_allowed():
            self._run_vlm(pdf_path, page_no, result)

        # ---------------------------------------------- normalisation pass
        self._finalize(result)
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        if not result.fields:
            result.warnings.append("هیچ فیلدی از این صفحه استخراج نشد.")
        return result

    # ------------------------------------------------------------- layer 1/2
    def _parse_words(self, words, *, page_no: int, template, confidence: float) -> LayerOutput:
        layer = LayerOutput(layer="text_layer")
        if not words:
            return layer
        regex_map = dict(getattr(template, "field_regex", None) or {}) if template else {}
        regex_map.setdefault("doc_code", self.settings.doc_code_pattern)
        parsed = parse_fields_from_words(
            words, page_no=page_no, template_regex=regex_map, confidence_base=confidence
        )
        for candidate in parsed.values():
            layer.add(candidate)
        layer.text = " ".join(w.text for w in words)
        layer.confidence = max((c.confidence for c in parsed.values()), default=0.0)
        return layer

    def _fill_from_text(self, result: PipelineResult, text: str, page_no: int) -> None:
        """Whole-page pattern search — rescues codes printed outside the ROI."""
        if "doc_code" not in result.fields:
            codes = extract_code_candidates(text, self.settings.doc_code_pattern)
            if codes:
                result.fields["doc_code"] = FieldCandidate(
                    name="doc_code",
                    value=codes[0],
                    confidence=0.72,
                    source=ExtractionSource.RULE,
                    page_no=page_no,
                    note="whole-page pattern search",
                )
                self._append_method(result, "pattern_search")
        if "rev" not in result.fields:
            import re

            match = re.search(r"\bREV(?:ISION)?\s*[.:\-]?\s*([A-Za-z0-9]{1,4})\b", text, re.IGNORECASE)
            if match:
                token = normalize_rev(match.group(1))
                if token:
                    result.fields["rev"] = FieldCandidate(
                        name="rev",
                        value=token,
                        confidence=0.7,
                        source=ExtractionSource.RULE,
                        page_no=page_no,
                        note="regex rev search",
                    )

    # ------------------------------------------------------------- layer 3
    def _run_ocr(self, pdf_path: str, page_no: int, result: PipelineResult) -> LayerOutput | None:
        if not self.ocr.is_available():
            result.warnings.append("موتور OCR در دسترس نیست؛ استخراج فقط بر پایه لایه متنی انجام شد.")
            return None
        region = DEFAULT_TITLE_BLOCK_REGION
        if result.template_id:
            template = next((t for t in self.templates if str(getattr(t, "id", "")) == result.template_id), None)
            regions = getattr(template, "regions", None) or {}
            region = regions.get("title_block") or region
        try:
            image = pdf_utils.render_region_norm(pdf_path, page_no, region, dpi=self.settings.ocr_dpi)
        except pdf_utils.PdfError as exc:
            result.warnings.append(f"رندر تصویر برای OCR ناموفق بود: {exc}")
            return None

        result.title_block_png = image
        layer = LayerOutput(layer="ocr")
        try:
            words = self.ocr.read_words(image, self.settings.ocr_langs, page_no=page_no)
        except Exception as exc:  # pragma: no cover - engine specific
            logger.warning("OCR failed: %s", exc)
            result.warnings.append(f"OCR ناموفق بود: {exc}")
            return None
        if not words:
            return None
        parsed = parse_fields_from_words(
            words, page_no=page_no, template_regex={"doc_code": self.settings.doc_code_pattern},
            confidence_base=0.78,
        )
        for candidate in parsed.values():
            candidate.source = ExtractionSource.OCR
            layer.add(candidate)
        layer.text = " ".join(w.text for w in words)
        layer.confidence = max((c.confidence for c in parsed.values()), default=0.0)
        layer.meta = {"engine": getattr(self.ocr, "name", "unknown"), "dpi": self.settings.ocr_dpi}
        self._append_method(result, "ocr")
        result.models["ocr"] = getattr(self.ocr, "name", "tesseract")
        return layer

    # ------------------------------------------------------------- layer 4
    def _vlm_allowed(self) -> bool:
        if not self.settings.vlm_fallback or self.vlm is None:
            return False
        if self.vlm_calls >= self.settings.vlm_max_calls:
            return False
        try:
            return bool(self.vlm.is_available())
        except Exception:  # pragma: no cover
            return False

    def _run_vlm(self, pdf_path: str, page_no: int, result: PipelineResult) -> None:
        region = DEFAULT_TITLE_BLOCK_REGION
        try:
            image = result.title_block_png or pdf_utils.render_region_norm(
                pdf_path, page_no, region, dpi=200
            )
        except pdf_utils.PdfError as exc:
            result.warnings.append(f"آماده‌سازی تصویر برای VLM ناموفق بود: {exc}")
            return
        try:
            from app.services.pdf_utils import image_to_png_bytes

            extracted = self.vlm.extract(image_to_png_bytes(image, max_dimension=1600))
            self.vlm_calls += 1
        except Exception as exc:  # pragma: no cover - network/model specific
            logger.warning("VLM extraction failed: %s", exc)
            result.warnings.append(f"VLM پاسخ معتبری برنگرداند: {exc}")
            return
        if not extracted:
            return

        field_alias = {"discipline": "discipline", "unit": "unit", "status": "doc_status"}
        for raw_name, (value, confidence) in extracted.items():
            name = field_alias.get(raw_name, raw_name)
            if name not in TARGET_FIELDS:
                continue
            existing = result.fields.get(name)
            if existing is not None and existing.confidence >= 0.7:
                continue  # deterministic layers win
            result.fields[name] = FieldCandidate(
                name=name,
                value=value,
                confidence=min(confidence, 0.85),
                source=ExtractionSource.VLM,
                page_no=page_no,
                note="vlm fallback",
            )
        self._append_method(result, "vlm")
        result.models["vlm"] = settings.vlm_model
        result.warnings.append("برخی فیلدها با مدل بینایی-زبانی خوانده شد؛ تأیید انسانی لازم است.")

    # ------------------------------------------------------------- helpers
    def _merge(self, result: PipelineResult, layer: LayerOutput) -> None:
        if layer.error:
            result.warnings.append(layer.error)
            return
        for name, candidate in layer.fields.items():
            existing = result.fields.get(name)
            if existing is None or candidate.confidence > existing.confidence:
                result.fields[name] = candidate

    def _append_method(self, result: PipelineResult, method: str) -> None:
        if method not in result.methods_used:
            result.methods_used.append(method)

    def _finalize(self, result: PipelineResult) -> None:
        """Normalise every value and build the confidence map."""
        for name, candidate in list(result.fields.items()):
            if name == "doc_code":
                normalized = normalize_code(candidate.value)
                if normalized:
                    candidate.value = normalized
                else:
                    result.fields.pop(name, None)
                    continue
            elif name == "rev":
                normalized = normalize_rev(candidate.value)
                if normalized:
                    candidate.value = normalized
                else:
                    result.fields.pop(name, None)
                    continue
            candidate.value = (candidate.value or "").strip()
            result.confidence[name] = round(max(0.0, min(1.0, candidate.confidence)), 4)

        for name in TARGET_FIELDS:
            result.confidence.setdefault(name, 0.0 if name in REQUIRED_FOR_AUTO else 0.0)

        # a text-layer candidate always outranks an OCR/VLM read of the same field
        for name, candidate in result.fields.items():
            if candidate.source is ExtractionSource.RULE and result.confidence.get(name, 0) > 0.9:
                result.confidence[name] = 0.9
