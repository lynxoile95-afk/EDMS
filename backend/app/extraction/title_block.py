"""Title-block detection and field parsing from positioned words.

Two complementary strategies:

* **Template mode** – a :class:`TitleBlockTemplate` supplies normalised regions
  and optional per-field regex. Highest precision, requires one-time setup.
* **Heuristic mode** – no template configured: find the English/Persian field
  labels anywhere on the sheet (``DOC NO``, ``کد مدرک`` …) and take the value to
  the right of / below the label. Works surprisingly well for first contact with
  a new contractor, but always scores lower.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.extraction.base import FieldCandidate
from app.models.enums import ExtractionSource
from app.services.normalization import (
    extract_code_candidates,
    normalize_code,
    normalize_rev,
    to_ascii_digits,
)

#: label vocabulary — order matters, the first match wins.
FIELD_LABELS: dict[str, list[str]] = {
    "doc_code": [
        r"document\s*(?:no|number|code)",
        r"doc(?:ument)?\.?\s*no\.?",
        r"drawing\s*(?:no|number)",
        r"dwg\.?\s*no\.?",
        r"drawing\s*code",
        r"کد\s*(?:مدرک|نقشه|سند)",
        r"شماره\s*(?:مدرک|نقشه|سند)",
        r"شماره\s*مدرک",
    ],
    "rev": [
        r"revision",
        r"rev\.?",
        r"بازنگری",
        r"ویرایش",
        r"تجدید\s*نظر",
    ],
    "title": [
        r"title",
        r"document\s*title",
        r"description",
        r"عنوان\s*(?:مدرک|نقشه)?",
        r"شرح",
        r"موضوع",
    ],
    "discipline": [
        r"discipline",
        r"دیسیپلین",
        r"رشته",
    ],
    "unit": [
        r"unit",
        r"یونیت",
        r"واحد",
    ],
    "doc_status": [
        r"status",
        r"وضعیت",
    ],
    "date": [
        r"date",
        r"تاریخ",
    ],
    "sheet_no": [
        r"sheet\s*(?:no|number)?",
        r"برگ",
    ],
    "sheet_count": [
        r"of\s*sheets",
        r"total\s*sheets",
        r"تعداد\s*برگ",
    ],
    "scale": [r"scale", r"مقیاس"],
    "project": [r"project", r"پروژه"],
}

_COMPILED_LABELS: dict[str, list[re.Pattern]] = {
    name: [re.compile(p, re.IGNORECASE) for p in patterns] for name, patterns in FIELD_LABELS.items()
}

#: value shapes used to validate a candidate taken from the right of a label
_VALUE_VALIDATORS = {
    "rev": re.compile(r"^[A-Za-z0-9]{1,5}$"),
    "sheet_no": re.compile(r"^\d{1,4}$"),
    "sheet_count": re.compile(r"^\d{1,4}$"),
    "date": re.compile(r"^\d{2,4}[/\-.]\d{1,2}[/\-.]\d{1,2}$"),
}


@dataclass(slots=True)
class Line:
    """A visual text line reconstructed from positioned words."""

    text: str
    y: float
    x_start: float
    x_end: float
    page_no: int
    words: list = field(default_factory=list)

    @property
    def bbox(self) -> list[float]:
        if not self.words:
            return [self.x_start, self.y, self.x_end, self.y]
        y0 = min(w.bbox[1] for w in self.words)
        y1 = max(w.bbox[3] for w in self.words)
        return [self.x_start, y0, self.x_end, y1]


def group_lines(words: list, tolerance: float = 2.5) -> list[Line]:
    """Group words into visual lines (top-to-bottom, left-to-right)."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (round(w.cy, 1), w.bbox[0]))
    lines: list[list] = []
    current: list = []
    last_y: float | None = None
    for word in ordered:
        if last_y is None or abs(word.cy - last_y) <= tolerance:
            current.append(word)
            last_y = word.cy if last_y is None else (last_y + word.cy) / 2
        else:
            lines.append(current)
            current = [word]
            last_y = word.cy
    if current:
        lines.append(current)
    result: list[Line] = []
    for group in lines:
        group.sort(key=lambda w: w.bbox[0])
        result.append(
            Line(
                text=" ".join(w.text for w in group),
                y=sum(w.cy for w in group) / len(group),
                x_start=min(w.bbox[0] for w in group),
                x_end=max(w.bbox[2] for w in group),
                page_no=group[0].page_no,
                words=group,
            )
        )
    return result


def _split_label_value(line: Line, pattern: re.Pattern) -> tuple[bool, str]:
    """Return ``(label_found, text_after_label_on_the_same_line)``."""
    text = line.text
    match = pattern.search(text)
    if not match:
        return False, ""
    remainder = text[match.end():]
    remainder = re.sub(r"^[\s:：\-–—|./]+", "", remainder).strip()
    return True, remainder


def _value_below(lines: list[Line], index: int, label_line: Line, max_lines: int = 3) -> Line | None:
    """First line below the label that starts within the label's x-range."""
    for offset in range(1, max_lines + 1):
        if index + offset >= len(lines):
            break
        candidate = lines[index + offset]
        if candidate.y - label_line.y > 60:  # too far: stop
            break
        if candidate.x_start >= label_line.x_start - 30:
            return candidate
    return None


def parse_fields_from_words(
    words: list,
    *,
    page_no: int = 1,
    template_regex: dict[str, str] | None = None,
    confidence_base: float = 0.9,
) -> dict[str, FieldCandidate]:
    """Label-driven extraction over a set of words (already restricted to an ROI)."""
    lines = group_lines(words)
    results: dict[str, FieldCandidate] = {}
    template_regex = template_regex or {}

    for index, line in enumerate(lines):
        for field_name, patterns in _COMPILED_LABELS.items():
            if field_name in results:
                continue
            for pattern in patterns:
                found, remainder = _split_label_value(line, pattern)
                if not found:
                    continue
                value = remainder
                confidence = confidence_base
                bbox = line.bbox
                if not value:
                    below = _value_below(lines, index, line)
                    if below is None:
                        continue
                    value = below.text
                    bbox = below.bbox
                    confidence = confidence_base - 0.08
                value = value.strip(" :：-–—|")
                cleaned = _normalise_field(field_name, value, template_regex.get(field_name))
                if not cleaned:
                    continue
                results[field_name] = FieldCandidate(
                    name=field_name,
                    value=cleaned,
                    confidence=confidence,
                    source=ExtractionSource.TEXT_LAYER,
                    bbox=bbox,
                    page_no=page_no,
                )
                break

    # Document code is the highest-value field: if the label scan failed, look
    # for a code-shaped token anywhere in the ROI before giving up.
    if "doc_code" not in results:
        joined = " ".join(line.text for line in lines)
        pattern = template_regex.get("doc_code")
        if pattern in (None, "", r"\S+"):
            pattern = ""  # generic dash-separated shape; never "any token"
        candidates = extract_code_candidates(joined, pattern)
        if candidates:
            results["doc_code"] = FieldCandidate(
                name="doc_code",
                value=candidates[0],
                confidence=confidence_base - 0.18,
                source=ExtractionSource.TEXT_LAYER,
                page_no=page_no,
                note="code pattern search (no label)",
            )
    return results


def _normalise_field(field_name: str, value: str, regex: str | None = None) -> str | None:
    raw = to_ascii_digits(value).strip()
    if not raw:
        return None
    if regex:
        match = re.search(regex, raw, re.IGNORECASE)
        if match:
            raw = match.group(0)
    if field_name == "doc_code":
        pattern = "" if regex in (None, "", r"\S+") else regex
        tokens = extract_code_candidates(raw, pattern)
        if tokens:
            return tokens[0]
        return normalize_code(raw) or None
    if field_name == "rev":
        token = normalize_rev(raw.split()[0] if raw.split() else raw)
        if token and _VALUE_VALIDATORS["rev"].match(token):
            return token
        return None
    if field_name in {"sheet_no", "sheet_count"}:
        digits = re.sub(r"\D", "", raw)
        return digits or None
    if field_name == "date":
        match = _VALUE_VALIDATORS["date"].search(raw)
        return match.group(0) if match else raw[:20]
    if field_name in {"doc_status", "discipline", "unit"}:
        token = raw.split()[0] if field_name in {"discipline", "unit"} else raw
        return token.strip()[:40]
    return raw[:200]


def select_template(templates: list, page_aspect_ratio: float, page_text: str) -> object | None:
    """Pick the best template for a page (aspect-ratio window + anchor keywords)."""
    best = None
    best_score = -1.0
    lowered = (page_text or "").lower()
    for template in templates:
        if not getattr(template, "is_active", True):
            continue
        score = 0.0
        ratios = getattr(template, "page_aspect_ratios", None) or []
        if ratios:
            in_window = False
            for window in ratios:
                if isinstance(window, (list, tuple)) and len(window) == 2:
                    if window[0] <= page_aspect_ratio <= window[1]:
                        in_window = True
                        break
                elif isinstance(window, (int, float)) and abs(page_aspect_ratio - window) < 0.05:
                    in_window = True
                    break
            if not in_window:
                continue
            score += 1.0
        anchors = getattr(template, "anchor_keywords", None) or []
        if anchors:
            hits = sum(1 for keyword in anchors if keyword.lower() in lowered)
            if hits == 0:
                continue
            score += hits / len(anchors)
        score += (100 - min(int(getattr(template, "priority", 100)), 100)) / 1000.0
        if score > best_score:
            best, best_score = template, score
    return best


def region_words(words: list, region: dict, page_width: float, page_height: float) -> list:
    x0 = float(region.get("x", 0.0)) * page_width
    y0 = float(region.get("y", 0.0)) * page_height
    x1 = x0 + float(region.get("w", 1.0)) * page_width
    y1 = y0 + float(region.get("h", 1.0)) * page_height
    return [w for w in words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1]
