"""Offline rendering for submitted PDF review markups.

The source bytes are opened read-only and a new PDF is built in memory.  The
vector marks are drawn directly into the new page content, so the result is a
flattened copy while the original storage key and hash are never changed.
"""

from __future__ import annotations

import io
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "DejaVuSans.ttf"


def _point(raw: Any) -> tuple[float, float]:
    if isinstance(raw, dict):
        return float(raw.get("x", 0)), float(raw.get("y", 0))
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return float(raw[0]), float(raw[1])
    return 0.0, 0.0


def _hex_color(value: str | None, fallback: str = "#d64545") -> tuple[float, float, float]:
    raw = value or fallback
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        raw = fallback
    channels = tuple(int(raw[index : index + 2], 16) / 255 for index in (1, 3, 5))
    # White and near-white strokes disappear on the source PDF. Preserve the
    # existing annotation record but render legacy values with a visible red
    # fallback until the reviewer chooses a palette color.
    if all(channel >= 238 / 255 for channel in channels):
        return _hex_color(fallback, fallback=fallback)
    return channels  # type: ignore[return-value]


def _stroke_width(value: Any, default: float = 2.5) -> float:
    try:
        width = float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return min(max(width, 1.0), 10.0)


def _rect(page: Any, points: list[Any]) -> Any:
    import fitz

    width = float(page.rect.width)
    height = float(page.rect.height)
    coords = [_point(item) for item in points] or [(0.05, 0.05), (0.25, 0.15)]
    xs = [max(0.0, min(1.0, item[0])) * width for item in coords]
    ys = [max(0.0, min(1.0, item[1])) * height for item in coords]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 - x0 < 2:
        x1 = min(width, x0 + 36)
    if y1 - y0 < 2:
        y1 = min(height, y0 + 24)
    return fitz.Rect(x0, y0, x1, y1)


def _page_points(page: Any, points: list[Any]) -> list[Any]:
    import fitz

    width = float(page.rect.width)
    height = float(page.rect.height)
    return [
        fitz.Point(
            max(0.0, min(1.0, _point(item)[0])) * width,
            max(0.0, min(1.0, _point(item)[1])) * height,
        )
        for item in points
    ]


def _point_rects(page: Any, points: list[Any]) -> list[Any]:
    """Turn normalized point pairs into independent text-selection rectangles."""
    if len(points) >= 4 and len(points) % 2 == 0:
        return [_rect(page, points[index : index + 2]) for index in range(0, len(points), 2)]
    return [_rect(page, points)]


def _shape(text: str) -> str:
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:  # pragma: no cover - optional shaping fallback
        return text


def _insert_text(page: Any, rect: Any, text: str, color: tuple[float, float, float], *, size: float = 10) -> None:
    if not text:
        return
    # PyMuPDF can embed the bundled font in the flattened copy.  Fit short
    # Persian notes into small callout/note boxes instead of silently dropping
    # the textbox when the requested size is too wide or tall.
    requested = max(5.0, min(float(size), 48.0))
    kwargs: dict[str, Any] = {
        "fontfile": str(FONT_PATH) if FONT_PATH.exists() else None,
        "fontsize": requested,
        "fontname": "EDMSMarkupFont" if FONT_PATH.exists() else "helv",
        "color": color,
        "align": 0,
        "overlay": True,
    }
    if kwargs["fontfile"] is None:
        kwargs.pop("fontfile")
    shaped = _shape(text)
    for candidate in sorted({requested, 16.0, 14.0, 12.0, 10.0, 8.0, 6.0, 5.0}, reverse=True):
        if candidate > requested:
            continue
        kwargs["fontsize"] = candidate
        try:
            remaining = page.insert_textbox(rect, shaped, **kwargs)
            if remaining >= 0:
                return
        except Exception:
            break
    # A final unbounded insert keeps the text visible even when a legacy
    # PyMuPDF/font combination rejects a textbox rectangle.
    fallback = dict(kwargs)
    fallback["fontsize"] = 5.0
    try:
        page.insert_text((rect.x0 + 2, rect.y0 + 7), shaped, **fallback)
    except Exception:
        fallback.pop("fontfile", None)
        fallback["fontname"] = "helv"
        page.insert_text((rect.x0 + 2, rect.y0 + 7), text, **fallback)


def _draw_arrow(page: Any, start: Any, end: Any, color: tuple[float, float, float], width: float) -> None:
    import fitz

    page.draw_line(start, end, color=color, width=width, overlay=True)
    angle = math.atan2(end.y - start.y, end.x - start.x)
    length = max(8, min(20, math.hypot(end.x - start.x, end.y - start.y) / 5 or 10))
    wing_a = fitz.Point(end.x - length * math.cos(angle - 0.48), end.y - length * math.sin(angle - 0.48))
    wing_b = fitz.Point(end.x - length * math.cos(angle + 0.48), end.y - length * math.sin(angle + 0.48))
    page.draw_polyline([wing_a, end, wing_b], color=color, width=width, overlay=True)


def _draw_annotation(page: Any, annotation: Any) -> None:
    import fitz

    points = annotation.get("points") or []
    kind = str(annotation.get("kind") or "")
    color = _hex_color(annotation.get("color"))
    line_width = _stroke_width(annotation.get("stroke_width"))
    rect = _rect(page, points)
    page_points = _page_points(page, points)
    text = str(annotation.get("text") or "")
    try:
        if kind == "highlight":
            for item_rect in _point_rects(page, points):
                page.draw_rect(item_rect, color=color, fill=color, width=line_width, fill_opacity=0.28, overlay=True)
        elif kind in {"underline", "strikethrough"}:
            for item_rect in _point_rects(page, points):
                y = item_rect.y1 if kind == "underline" else item_rect.y0 + item_rect.height * 0.52
                page.draw_line(
                    fitz.Point(item_rect.x0, y),
                    fitz.Point(item_rect.x1, y),
                    color=color,
                    width=line_width,
                    overlay=True,
                )
        elif kind in {"rectangle", "revision_cloud"}:
            if kind == "rectangle":
                page.draw_rect(rect, color=color, width=line_width, overlay=True)
            else:
                # A light cloud approximation made from repeated circular arcs
                # stays portable across PyMuPDF versions and is visibly distinct.
                page.draw_rect(rect, color=color, width=line_width, overlay=True)
                radius = min(10, max(4, min(rect.width, rect.height) / 8))
                x = rect.x0 + radius
                while x < rect.x1 - radius:
                    page.draw_circle(fitz.Point(x, rect.y0), radius, color=color, width=line_width, overlay=True)
                    page.draw_circle(fitz.Point(x, rect.y1), radius, color=color, width=line_width, overlay=True)
                    x += radius * 1.7
                y = rect.y0 + radius
                while y < rect.y1 - radius:
                    page.draw_circle(fitz.Point(rect.x0, y), radius, color=color, width=line_width, overlay=True)
                    page.draw_circle(fitz.Point(rect.x1, y), radius, color=color, width=line_width, overlay=True)
                    y += radius * 1.7
        elif kind == "ellipse":
            page.draw_oval(rect, color=color, width=line_width, overlay=True)
        elif kind == "arrow":
            if len(page_points) >= 2:
                start, end = page_points[0], page_points[-1]
            else:
                start, end = fitz.Point(rect.x0, rect.y0), fitz.Point(rect.x1, rect.y1)
            _draw_arrow(page, start, end, color, line_width)
        elif kind == "callout":
            # Callout points are [leader anchor, box top-left, box bottom-right].
            box_points = points[1:] if len(points) >= 3 else points
            box = _rect(page, box_points)
            anchor = page_points[0] if page_points else fitz.Point(box.x0, box.y0)
            target = fitz.Point(min(max(anchor.x, box.x0), box.x1), min(max(anchor.y, box.y0), box.y1))
            _draw_arrow(page, anchor, target, color, line_width)
            page.draw_rect(box, color=color, fill=(1.0, 1.0, 1.0), width=line_width, fill_opacity=0.92, overlay=True)
            _insert_text(page, box + (4, 3, -4, -3), text or "نظر", color, size=max(8, min(18, box.height * 0.55)))
        elif kind == "freehand":
            if len(page_points) >= 2:
                page.draw_polyline(page_points, color=color, width=line_width, closePath=False, overlay=True)
        elif kind == "sticky_note":
            page.draw_rect(rect, color=color, fill=(1.0, 0.96, 0.65), width=line_width, fill_opacity=0.86, overlay=True)
            _insert_text(page, rect + (4, 3, -4, -3), text or "نظر", color, size=9)
        elif kind == "free_text":
            _insert_text(page, rect, text or "نظر", color, size=max(8, min(18, rect.height * 0.55)))
        else:
            page.draw_rect(rect, color=color, width=line_width, overlay=True)
            _insert_text(page, rect, text, color, size=9)
    except TypeError:
        # Older PyMuPDF builds do not expose opacity/overlay in every primitive.
        if kind in {"rectangle", "revision_cloud", "ellipse", "highlight"}:
            page.draw_rect(rect, color=color, width=line_width)
        elif kind in {"underline", "strikethrough"}:
            y = rect.y1 if kind == "underline" else rect.y0 + rect.height * 0.52
            page.draw_line(fitz.Point(rect.x0, y), fitz.Point(rect.x1, y), color=color, width=line_width)
        elif kind == "freehand" and len(page_points) >= 2:
            page.draw_polyline(page_points, color=color, width=line_width)
        elif kind in {"arrow", "callout"} and len(page_points) >= 2:
            page.draw_line(page_points[0], page_points[-1], color=color, width=line_width)
        elif kind in {"sticky_note", "free_text", "callout"}:
            _insert_text(page, rect, text or "نظر", color, size=9)


def render_annotated_pdf(source_bytes: bytes, annotations: list[dict]) -> bytes:
    """Return a new flattened PDF; ``source_bytes`` is never modified."""
    import fitz

    document = fitz.open(stream=source_bytes, filetype="pdf")
    if document.needs_pass:
        raise ValueError("فایل PDF با رمز محافظت شده است و قابل نشانه‌گذاری نیست.")
    for raw in annotations:
        page_no = int(raw.get("page_no") or 0)
        if page_no < 1 or page_no > document.page_count:
            raise ValueError("شماره صفحه نشانه‌گذاری معتبر نیست.")
        _draw_annotation(document.load_page(page_no - 1), raw)
    try:
        result = document.tobytes(garbage=4, deflate=True, clean=True)
    finally:
        document.close()
    return result


def _safe_token(value: str, fallback: str = "user") -> str:
    value = re.sub(r"[^\w\-.\u0600-\u06ff]+", "_", value, flags=re.UNICODE).strip("._")
    return value[:80] or fallback


def output_filenames(original_filename: str, user_name: str, when: datetime | None = None) -> tuple[str, str]:
    stem = Path(original_filename).stem or "document"
    date_part = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
    author = _safe_token(user_name)
    return (
        f"{_safe_token(stem, 'document')}_COMMENT_{author}_{date_part}.pdf",
        f"{_safe_token(stem, 'document')}_COMMENT_SHEET_{author}_{date_part}.pdf",
    )


def _font_and_shape():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_name = "Helvetica"
    if FONT_PATH.exists():
        try:
            if "EDMSMarkupFont" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("EDMSMarkupFont", str(FONT_PATH)))
            font_name = "EDMSMarkupFont"
        except Exception:  # pragma: no cover - font parser is host-dependent
            logger.warning("Bundled Persian-capable font could not be registered")

    def shape(text: str) -> str:
        return _shape(str(text))

    return font_name, shape


def render_comment_sheet(
    *,
    project_name: str,
    transmittal_number: str | None,
    document_code: str | None,
    revision_label: str | None,
    original_filename: str,
    author_name: str,
    annotations: list[dict],
    submitted_at: datetime | None,
) -> bytes:
    """Create a Persian-capable, searchable Comment Sheet PDF."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    font_name, shape = _font_and_shape()
    buffer = io.BytesIO()
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=(page_width, page_height))
    left = 10 * mm
    right = page_width - 10 * mm
    top = page_height - 12 * mm
    row_height = 15
    columns = [
        ("#", 24),
        ("صفحه", 42),
        ("نوع", 82),
        ("شرح نظر / Comment", 205),
        ("نویسنده", 90),
        ("وضعیت", 70),
        ("پاسخ مالک", 190),
    ]
    total = sum(width for _, width in columns)
    widths = [width / total * (right - left) for _, width in columns]

    def text(value: Any) -> str:
        return shape(str(value or "—"))

    def header(y: float) -> float:
        pdf.setFillColor(colors.HexColor("#1f4e79"))
        pdf.rect(left, y - row_height, right - left, row_height, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont(font_name, 7.5)
        x = left
        for (label, _), width in zip(columns, widths):
            pdf.drawCentredString(x + width / 2, y - 10, text(label))
            x += width
        pdf.setFillColor(colors.black)
        return y - row_height

    pdf.setTitle("Comment Sheet")
    pdf.setFont(font_name, 16)
    pdf.setFillColor(colors.HexColor("#1f3864"))
    pdf.drawCentredString(page_width / 2, top, text("برگه ثبت نظرات / Comment Sheet"))
    pdf.setFont(font_name, 8.5)
    pdf.setFillColor(colors.black)
    meta = (
        f"پروژه: {project_name}    ترانسمیتال: {transmittal_number or '—'}    "
        f"کد مدرک: {document_code or '—'}    بازنگری: {revision_label or '—'}"
    )
    pdf.drawRightString(right, top - 16, text(meta))
    pdf.drawRightString(right, top - 29, text(f"فایل اصلی: {original_filename}    نویسنده: {author_name}"))
    pdf.drawRightString(right, top - 42, text(f"تاریخ ثبت: {submitted_at.isoformat() if submitted_at else '—'}"))

    y = top - 56
    y = header(y)
    pdf.setFont(font_name, 7.2)
    for index, raw in enumerate(annotations, start=1):
        values = [
            str(raw.get("number") or index),
            str(raw.get("page_no") or "—"),
            str(raw.get("kind") or "—"),
            str(raw.get("text") or "—"),
            str(raw.get("author_name") or author_name or "—"),
            str(raw.get("reply_status") or "open"),
            str(raw.get("reply_text") or "—"),
        ]
        if y < 22 * mm:
            pdf.setFont(font_name, 7)
            pdf.drawRightString(right, 12 * mm, text("ادامه برگه ثبت نظرات / Comment Sheet"))
            pdf.showPage()
            y = page_height - 16 * mm
            y = header(y)
            pdf.setFont(font_name, 7.2)
        x = left
        pdf.setFillColor(colors.HexColor("#f7fafd") if index % 2 == 0 else colors.white)
        pdf.rect(left, y - row_height, right - left, row_height, fill=1, stroke=0)
        pdf.setFillColor(colors.black)
        for value, width in zip(values, widths):
            pdf.setStrokeColor(colors.HexColor("#cfd6de"))
            pdf.rect(x, y - row_height, width, row_height, fill=0, stroke=1)
            pdf.drawCentredString(x + width / 2, y - 10, text(value[:110]))
            x += width
        y -= row_height
    pdf.setFont(font_name, 7)
    pdf.setFillColor(colors.HexColor("#666666"))
    pdf.drawRightString(right, 8 * mm, text("این برگه به‌صورت خودکار توسط سامانه EDMS تولید شده است."))
    pdf.save()
    return buffer.getvalue()
