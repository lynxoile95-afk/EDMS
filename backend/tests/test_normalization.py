"""Digit/dash/letter normalisation and document-code parsing."""

from __future__ import annotations

import pytest

from app.services.normalization import (
    clean_persian_text,
    extract_code_candidates,
    is_valid_code,
    normalize_code,
    normalize_rev,
    normalize_title,
    ocr_confusion_variants,
    to_ascii_digits,
)

PATTERN = (
    r"^(?P<project>[A-Z0-9]{2,6})-(?P<discipline>[A-Z]{2,4})-"
    r"(?P<type>[A-Z]{1,4})-(?P<seq>\d{3,5})$"
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PRJ-CIV-DR-001", "PRJ-CIV-DR-001"),
        ("prj-civ-dr-001", "PRJ-CIV-DR-001"),
        ("PRJ–CIV–DR–001", "PRJ-CIV-DR-001"),          # en dashes
        ("PRJ—CIV—DR—001", "PRJ-CIV-DR-001"),          # em dashes
        ("PRJ_CIV_DR_001", "PRJ-CIV-DR-001"),          # underscores
        ("PRJ-CIV-DR-۰۰۱", "PRJ-CIV-DR-001"),          # Persian digits
        ("PRJ-CIV-DR-٠٠١", "PRJ-CIV-DR-001"),          # Arabic-Indic digits
        ("  PRJ – CIV – DR – 001  ", "PRJ-CIV-DR-001"),
        ("PRJ--CIV--DR--001", "PRJ-CIV-DR-001"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_code(raw, expected):
    assert normalize_code(raw) == expected


def test_to_ascii_digits_mixed():
    assert to_ascii_digits("Rev ۱۲ / 12 / ١٢") == "Rev 12 / 12 / 12"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rev. A", "A"),
        ("REVISION B", "B"),
        ("r01", "R01"),
        ("۰۲", "02"),
        ("C1", "C1"),
        ("Rev- A", "A"),
        ("بازنگری پ", ""),  # only Latin/digit revisions are valid tokens
        ("", ""),
    ],
)
def test_normalize_rev(raw, expected):
    assert normalize_rev(raw) == expected


def test_normalize_title_folds_arabic_letters():
    assert normalize_title("چیدمان لوله‌كشي") == normalize_title("چیدمان لوله‌کشی")
    assert normalize_title("Plot  Plan / پلان") == "plot plan پلان"


def test_is_valid_code():
    assert is_valid_code("PRJ-CIV-DR-001", PATTERN)
    assert not is_valid_code("PRJ-CIV-DR-1", PATTERN)
    assert not is_valid_code("PLOT PLAN", PATTERN)


def test_extract_code_candidates_prefers_project_pattern():
    text = "PROJECT XYZ  DOCUMENT NO: PRJ–MEC–DR–۰۱۲۳   REV: B"
    assert extract_code_candidates(text, PATTERN)[0] == "PRJ-MEC-DR-0123"


def test_extract_code_candidates_generic_fallback():
    found = extract_code_candidates("see ABC-DEF-12 and XYZ-123-456", r"\S+")
    assert "ABC-DEF-12" in found


def test_ocr_confusion_variants_are_bounded_and_useful():
    variants = ocr_confusion_variants("PRJ-CIV-DR-00I")
    assert len(variants) <= 40
    assert "PRJ-CIV-DR-001" in variants


def test_clean_persian_text_keeps_text_and_normalises_digits():
    cleaned = clean_persian_text("كادر مشخصات ۱۲۳\nRev A")
    assert "کادر مشخصات 123" in cleaned
