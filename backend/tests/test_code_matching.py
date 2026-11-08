"""Code matching against the MDL: exact, alias, OCR variant, fuzzy."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import MDLItem
from app.services.code_matching import CodeMatcher


def test_exact_match_wins(db: Session, project):
    matcher = CodeMatcher(db, project.id)
    candidates = matcher.match("TST-CIV-DR-001")
    assert candidates[0].method == "exact"
    assert candidates[0].score == 1.0
    assert candidates[0].doc_code == "TST-CIV-DR-001"


def test_exact_match_is_normalisation_insensitive(db: Session, project):
    matcher = CodeMatcher(db, project.id)
    assert matcher.match("tst–civ–dr–۰۰۱")[0].method == "exact"


def test_alias_match(db: Session, project):
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    item.aliases = ["OLD-CIV-0001"]
    db.commit()
    matcher = CodeMatcher(db, project.id)
    candidates = matcher.match("OLD-CIV-0001")
    assert candidates[0].method == "alias"
    assert candidates[0].doc_code == "TST-CIV-DR-001"


def test_ocr_variant_match(db: Session, project):
    matcher = CodeMatcher(db, project.id)
    candidates = matcher.match("TST-CIV-DR-00I")  # letter I instead of digit 1
    assert candidates[0].method == "ocr_variant"
    assert candidates[0].doc_code == "TST-CIV-DR-001"


def test_fuzzy_suggests_closest(db: Session, project):
    matcher = CodeMatcher(db, project.id)
    candidates = matcher.match("TST-CIV-DR-003")  # does not exist
    assert candidates, "expected at least one fuzzy candidate"
    assert candidates[0].method in {"fuzzy", "ocr_variant"}
    assert candidates[0].doc_code.startswith("TST-CIV-DR-00")


def test_title_boosts_fuzzy_score(db: Session, project):
    matcher = CodeMatcher(db, project.id)
    with_title = matcher.match("TST-CIV-DR-009", "Plot Plan")[0]
    without_title = matcher.match("TST-CIV-DR-009", None)[0]
    assert with_title.score >= without_title.score


def test_unknown_project_returns_no_candidates(db: Session, project):
    import uuid

    matcher = CodeMatcher(db, uuid.uuid4())
    assert matcher.match("TST-CIV-DR-001") == []


def test_empty_code_returns_empty_list(db: Session, project):
    matcher = CodeMatcher(db, project.id)
    assert matcher.match("") == []
    assert matcher.match(None) == []


def test_candidates_are_sorted_by_score(db: Session, project):
    matcher = CodeMatcher(db, project.id)
    scores = [c.score for c in matcher.match("TST-CIV-DR-01")]
    assert scores == sorted(scores, reverse=True)
