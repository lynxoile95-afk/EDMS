"""Normalisation utilities.

Engineering drawings are bilingual, produced by many CAD tools, and often
scanned.  The same document code can therefore appear as::

    PRJ-DIS-A-001     prj–dis–a–001      PRJ‑DIS‑A‑001
    PRJ–DIS–A–۰۰۱     PRJ_DIS_A_001        PRJ  —  DIS — A — 001

Everything downstream (matching, revision comparison, uniqueness) depends on a
*single* canonical form, produced here.  This module is pure (no I/O, no DB) and
is unit-tested directly.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Character maps
# ---------------------------------------------------------------------------

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(PERSIAN_DIGITS)}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate(ARABIC_DIGITS)})

#: every dash-like character seen in real title blocks (hyphen, non-breaking
#: hyphen, figure dash, en/em dash, horizontal bar, minus sign, small/fullwidth
#: hyphen). Includes U+2010..U+2015, U+2212, U+FE58, U+FE63, U+FF0D.
DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d\u2043"
_DASH_MAP = {ord(c): "-" for c in DASHES}
_DASH_MAP[ord("_")] = "-"  # underscore is used interchangeably by some CAD tools

#: Arabic/Persian letter variants that must be folded for titles.
_LETTER_MAP = {
    ord("ي"): "ی",  # Arabic yeh  -> Persian yeh
    ord("ى"): "ی",  # alef maksura
    ord("ك"): "ک",  # Arabic kaf  -> Persian kaf
    ord("ۀ"): "ه",
    ord("ة"): "ه",
    ord("أ"): "ا",
    ord("إ"): "ا",
    ord("آ"): "ا",
    ord("ؤ"): "و",
    ord("ئ"): "ی",
}

_ZWNJ = "\u200c\u200d\u200e\u200f\ufeff"
_SPACE_MAP = {ord(c): " " for c in _ZWNJ}
_SPACE_MAP[ord("\u00a0")] = " "  # non-breaking space

_WS_RE = re.compile(r"\s+")
_MULTI_DASH_RE = re.compile(r"-{2,}")
_CODE_ALLOWED_RE = re.compile(r"[^A-Z0-9\-./]")
_TITLE_STRIP_RE = re.compile(r"[^\w\u0600-\u06FF\s]+", re.UNICODE)

#: characters that OCR commonly confuses inside document *codes*.
#: Applied only when we look for a code pattern, never to free text.
OCR_CONFUSIONS = {
    "O": "0",
    "Q": "0",
    "I": "1",
    "L": "1",
    "|": "1",
    "S": "5",
    "Z": "2",
    "B": "8",
    "G": "6",
}

#: "Rev."/"Revision"/Persian labels are prefixes; a bare leading "R" is only a
#: prefix when it is separated ("R. A", "R-A") so that tokens like "R01" survive.
REV_PREFIX_RE = re.compile(
    r"^(?:REV(?:ISION)?|رِ?و?یژن|بازنگری|ویرایش)\s*[.:\-]?\s*|^R\s*[.:\-]\s*",
    re.IGNORECASE,
)


def to_ascii_digits(text: str | None) -> str:
    """Convert Persian/Arabic-Indic digits to ASCII digits."""
    if not text:
        return ""
    return text.translate(_DIGIT_MAP)


def normalize_dashes(text: str | None) -> str:
    """Map every dash variant to ``-`` and collapse runs of dashes."""
    if not text:
        return ""
    return _MULTI_DASH_RE.sub("-", text.translate(_DASH_MAP))


def normalize_spaces(text: str | None) -> str:
    """Collapse all whitespace (including ZWNJ / NBSP) to single spaces."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text.translate(_SPACE_MAP)).strip()


def normalize_code(raw: str | None) -> str:
    """Canonical document code: upper-case, ASCII digits, single dashes.

    >>> normalize_code("prj–dis–a–۰۰۱")
    'PRJ-DIS-A-001'
    >>> normalize_code(" PRJ_DIS_A_001 ")
    'PRJ-DIS-A-001'
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = to_ascii_digits(text)
    text = normalize_spaces(text)
    text = normalize_dashes(text)
    text = text.upper()
    text = _CODE_ALLOWED_RE.sub("", text)
    text = _MULTI_DASH_RE.sub("-", text)
    text = text.strip("-./")
    return text


def normalize_title(raw: str | None) -> str:
    """Folded title for fuzzy comparison (keeps Persian/Arabic letters)."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = to_ascii_digits(text)
    text = text.translate(_LETTER_MAP)
    text = normalize_dashes(text)
    text = normalize_spaces(text)
    text = _TITLE_STRIP_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip().lower()
    return text


def normalize_rev(raw: str | None) -> str:
    """Canonical revision token.

    ``Rev. A`` -> ``A`` · ``r01`` -> ``R01`` · ``۰۲`` -> ``02``
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = to_ascii_digits(text)
    text = normalize_spaces(text)
    text = norm_letters(text).upper()
    text = REV_PREFIX_RE.sub("", text)
    text = normalize_dashes(text).strip("-.: ")
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text


def norm_letters(text: str | None) -> str:
    """Fold Arabic/Persian letter variants without changing digits."""
    if not text:
        return ""
    return text.translate(_LETTER_MAP)


def ocr_confusion_variants(code: str) -> list[str]:
    """Generate code variants where ambiguous glyphs are swapped.

    Used only by the matcher as a *fallback candidate generator*; the original
    code is always tried first.  Variant count is bounded to avoid explosion.
    """
    norm = normalize_code(code)
    if not norm:
        return []
    variants: set[str] = set()
    positions = [(i, ch) for i, ch in enumerate(norm) if ch in OCR_CONFUSIONS]
    # single substitutions (cheap and usually enough)
    for i, ch in positions:
        variants.add(norm[:i] + OCR_CONFUSIONS[ch] + norm[i + 1 :])
    # digit -> confusable letter is also common (0 -> O, 1 -> I)
    reverse = {v: k for k, v in OCR_CONFUSIONS.items() if k.isalpha()}
    for i, ch in enumerate(norm):
        if ch in {"0", "1", "5", "2", "8", "6"}:
            letter = reverse.get(ch)
            if letter:
                variants.add(norm[:i] + letter + norm[i + 1 :])
    variants.discard(norm)
    return sorted(variants)[:40]


def is_valid_code(code_norm: str, pattern: str) -> bool:
    """Deterministic validity check against the project's code pattern."""
    if not code_norm or not pattern:
        return bool(code_norm)
    try:
        return re.fullmatch(pattern, code_norm) is not None
    except re.error:
        return True  # a broken project pattern must never block ingestion


def extract_code_candidates(text: str, pattern: str, limit: int = 10) -> list[str]:
    """Find substrings of `text` that look like document codes.

    Two passes: (1) the project pattern searched over the raw, dash-normalised
    text; (2) a generic ``AAA-BBB-CCC-123`` shape when the configured pattern
    yields nothing (e.g. wrong per-contractor pattern).
    """
    if not text:
        return []
    prepared = normalize_dashes(to_ascii_digits(normalize_spaces(text))).upper()
    found: list[str] = []
    if pattern:
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            for match in compiled.finditer(prepared):
                candidate = normalize_code(match.group(0))
                if candidate and candidate not in found:
                    found.append(candidate)
                if len(found) >= limit:
                    return found
        except re.error:
            pass
    if not found:
        generic = re.compile(r"\b[A-Z0-9]{2,8}(?:-[A-Z0-9]{1,8}){2,5}\b")
        for match in generic.finditer(prepared):
            candidate = normalize_code(match.group(0))
            # require at least one digit to avoid matching prose
            if candidate and any(ch.isdigit() for ch in candidate) and candidate not in found:
                found.append(candidate)
            if len(found) >= limit:
                break
    return found


def clean_persian_text(text: str | None) -> str:
    """Display-quality cleanup for Persian OCR output (keeps meaning)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = norm_letters(text)
    text = to_ascii_digits(text)  # display layer re-renders Persian digits
    text = normalize_dashes(text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return _WS_RE.sub(" ", text.replace("\n", " \n ")).replace(" \n ", "\n").strip()
