"""GREEN / YELLOW / RED decisions and the rule catalogue."""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import Decision, Severity
from app.services.code_matching import MatchCandidate
from app.services.decision_engine import (
    DocumentFacts,
    compute_overall_confidence,
    decide,
)
from app.services.project_settings import merge_settings

ITEM_ID = uuid.uuid4()
EXACT = [
    MatchCandidate(ITEM_ID, "TST-CIV-DR-001", "Plot Plan", "CIV", 1.0, "exact"),
]

HIGH_CONFIDENCE = {
    "doc_code": 0.98,
    "rev": 0.96,
    "title": 0.92,
    "discipline_code": 0.9,
    "doc_status": 0.85,
    "unit_code": 0.85,
}


def _facts(**overrides) -> DocumentFacts:
    base = dict(
        doc_code="TST-CIV-DR-001",
        rev="B",
        title="Plot Plan",
        discipline_code="CIV",
        field_confidence=dict(HIGH_CONFIDENCE),
        methods_used=["text_layer"],
        filename="TST-CIV-DR-001-RevB.pdf",
        title_block_found=True,
    )
    base.update(overrides)
    return DocumentFacts(**base)


def rules(outcome) -> set[str]:
    return {i.rule_code for i in outcome.issues}


# --------------------------------------------------------------------------
def test_green_when_everything_matches():
    outcome = decide(
        _facts(),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        mdl_discipline_code="CIV",
        mdl_unit_code="U-100",
        mdl_status="Draft",
        candidates=EXACT,
        settings=merge_settings({"always_require_dcc_approval": False}),
    )
    assert outcome.decision is Decision.GREEN
    assert outcome.overall_confidence > 0.9
    assert outcome.requires_dcc is False


def test_default_project_setting_still_requires_dcc():
    outcome = decide(_facts(), mdl_current_rev="A", mdl_title="Plot Plan", candidates=EXACT)
    assert outcome.requires_dcc is True  # always_require_dcc_approval defaults to True


def test_missing_code_is_red_with_r01():
    outcome = decide(_facts(doc_code=None), candidates=[])
    assert outcome.decision is Decision.RED
    assert "R01" in rules(outcome)


def test_code_not_in_mdl_is_red_quarantine():
    outcome = decide(_facts(doc_code="TST-CIV-DR-999"), candidates=[])
    assert outcome.decision is Decision.RED
    assert "R03" in rules(outcome)


def test_near_match_is_yellow_not_red():
    candidate = MatchCandidate(ITEM_ID, "TST-CIV-DR-001", "Plot Plan", "CIV", 0.93, "ocr_variant")
    outcome = decide(
        _facts(doc_code="TST-CIV-DR-00I"), candidates=[candidate], mdl_title="Plot Plan"
    )
    assert outcome.decision is Decision.YELLOW
    assert "R04" in rules(outcome)


def test_invalid_pattern_is_red():
    outcome = decide(_facts(doc_code="THIS-IS-NOT-A-CODE"), candidates=[])
    assert "R02" in rules(outcome)


def test_lower_revision_is_red():
    outcome = decide(_facts(rev="A"), mdl_current_rev="B", mdl_title="Plot Plan", candidates=EXACT)
    assert "R07" in rules(outcome)
    assert outcome.decision is Decision.RED


def test_equal_revision_warns():
    outcome = decide(_facts(rev="A"), mdl_current_rev="A", mdl_title="Plot Plan", candidates=EXACT)
    assert "R06" in rules(outcome)
    assert outcome.decision is Decision.YELLOW


def test_revision_skip_warns_but_is_not_fatal():
    outcome = decide(_facts(rev="C"), mdl_current_rev="A", mdl_title="Plot Plan", candidates=EXACT)
    assert "R08" in rules(outcome)
    assert outcome.decision is Decision.YELLOW


def test_title_mismatch_warns():
    outcome = decide(
        _facts(title="Completely Different Drawing"),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        candidates=EXACT,
    )
    assert "R09" in rules(outcome)


def test_discipline_mismatch_configurable():
    warn = decide(
        _facts(discipline_code="MEC"),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        mdl_discipline_code="CIV",
        candidates=EXACT,
    )
    assert "R10" in rules(warn)
    assert warn.decision is Decision.YELLOW

    error = decide(
        _facts(discipline_code="MEC"),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        mdl_discipline_code="CIV",
        candidates=EXACT,
        settings=merge_settings({"discipline_mismatch_severity": "error"}),
    )
    assert error.decision is Decision.RED


def test_blocked_status_is_red():
    outcome = decide(
        _facts(doc_status="FINAL"),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        candidates=EXACT,
    )
    assert "R12" in rules(outcome)
    assert outcome.decision is Decision.RED


def test_filename_mismatch_warns():
    outcome = decide(
        _facts(filename="scan-0001.pdf"),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        candidates=EXACT,
    )
    assert "R13" in rules(outcome)


def test_duplicate_hash_is_red():
    outcome = decide(
        _facts(duplicate_of=str(uuid.uuid4())),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        candidates=EXACT,
    )
    assert "R14" in rules(outcome)
    assert outcome.decision is Decision.RED


def test_duplicate_code_in_session_is_red():
    outcome = decide(
        _facts(in_session_duplicate=True),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        candidates=EXACT,
    )
    assert "R18" in rules(outcome)


def test_low_field_confidence_yields_yellow():
    confidence = dict(HIGH_CONFIDENCE)
    confidence["rev"] = 0.3
    outcome = decide(_facts(field_confidence=confidence), mdl_current_rev="A", candidates=EXACT)
    assert "R15" in rules(outcome)
    assert outcome.decision is Decision.YELLOW


def test_vlm_usage_forces_human_confirmation():
    outcome = decide(
        _facts(methods_used=["text_layer", "vlm"]),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        candidates=EXACT,
    )
    assert "R17" in rules(outcome)
    assert outcome.decision is Decision.YELLOW


def test_missing_title_block_is_warning_not_error():
    outcome = decide(
        _facts(title_block_found=False), mdl_current_rev="A", mdl_title="Plot Plan", candidates=EXACT
    )
    assert "R20" in rules(outcome)
    severity = {i.rule_code: i.severity for i in outcome.issues}
    assert severity["R20"] is Severity.WARNING


def test_extraction_failure_is_red():
    outcome = decide(_facts(extraction_failed=True), candidates=[])
    assert "R22" in rules(outcome)
    assert outcome.decision is Decision.RED


# --------------------------------------------------------------------------
def test_overall_confidence_weighting():
    assert compute_overall_confidence({}) == 0.0
    perfect = {field: 1.0 for field in HIGH_CONFIDENCE}
    assert compute_overall_confidence(perfect) == pytest.approx(1.0)
    # a confident title without any code must not look good
    assert compute_overall_confidence({"title": 1.0}) < 0.2


def test_thresholds_are_configurable():
    strict = decide(
        _facts(),
        mdl_current_rev="A",
        mdl_title="Plot Plan",
        candidates=EXACT,
        settings=merge_settings({"thresholds": {"green": 0.999}}),
    )
    assert strict.decision is Decision.YELLOW


def test_issue_serialisation():
    outcome = decide(_facts(doc_code=None), candidates=[])
    payload = outcome.issues[0].as_dict()
    assert payload["rule_code"] == "R01"
    assert payload["status"] == "open"
    assert payload["message_fa"]
