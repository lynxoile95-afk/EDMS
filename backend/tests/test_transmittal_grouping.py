"""Transmittal grouping, DDM resolution, due dates and numbering."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from app.models import (
    DDMRule,
    DDMRuleRecipient,
    DocumentRevision,
    MDLItem,
    NumberSequence,
    Project,
    Transmittal,
)
from app.models.enums import ActionRequired, GroupingStrategy, PurposeOfIssue, TransmittalType
from app.services import numbering, transmittal_service
from app.services.ddm import add_working_days, resolve
from app.services.project_settings import merge_settings


def make_revision(db: Session, project: Project, doc_code: str, rev: str = "B") -> DocumentRevision:
    item = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm=doc_code).one()
    revision = DocumentRevision(
        project_id=project.id,
        mdl_item_id=item.id,
        rev=rev,
        rev_seq=2,
        file_hash=uuid.uuid4().hex * 2,
        page_count=1,
        is_current=True,
    )
    db.add(revision)
    db.commit()
    db.refresh(revision)
    return revision


# --------------------------------------------------------------------------
def test_ddm_resolution_picks_most_specific(db: Session, project, users, ddm_rules):
    fallback = DDMRule(project_id=project.id, name="fallback", due_days=30, priority=1)
    db.add(fallback)
    db.flush()
    db.add(DDMRuleRecipient(rule_id=fallback.id, user_id=users["dcc"].id, action=ActionRequired.INFO))
    db.commit()

    resolution = resolve(
        db,
        project_id=project.id,
        discipline_id=ddm_rules.discipline_id,
        doc_type="DWG",
        purpose=PurposeOfIssue.IFR,
    )
    assert resolution.matched
    assert resolution.rule_name == "CIV drawings"
    assert {r.user_id for r in resolution.recipients} == {users["reviewer"].id, users["pm"].id}


def test_ddm_falls_back_when_nothing_specific(db: Session, project, users):
    fallback = DDMRule(project_id=project.id, name="fallback", due_days=21)
    db.add(fallback)
    db.flush()
    db.add(DDMRuleRecipient(rule_id=fallback.id, user_id=users["dcc"].id, action=ActionRequired.INFO))
    db.commit()
    resolution = resolve(db, project_id=project.id, doc_type="DWG", purpose=PurposeOfIssue.IFA)
    assert resolution.matched
    assert resolution.due_days == 21


def test_ddm_unmatched_is_reported_not_silent(db: Session, project):
    resolution = resolve(db, project_id=project.id, doc_type="DWG", purpose=PurposeOfIssue.IFC)
    assert resolution.matched is False
    assert resolution.explanation


def test_working_days_skips_thursday_and_friday():
    start = date(2026, 10, 21)  # Wednesday
    assert add_working_days(start, 1) == date(2026, 10, 24)  # Saturday
    assert add_working_days(start, 2) == date(2026, 10, 25)  # Sunday


def test_grouping_by_recipients_merges_same_discipline(db: Session, project, users, ddm_rules):
    revisions = [
        make_revision(db, project, "TST-CIV-DR-001"),
        make_revision(db, project, "TST-CIV-DR-002"),
    ]
    plans = transmittal_service.plan_groups(
        db, project=project, revisions=revisions, purpose=PurposeOfIssue.IFR
    )
    assert len(plans) == 1
    assert len(plans[0].revisions) == 2
    assert plans[0].actions[revisions[0].id] == ActionRequired.REVIEW


def test_grouping_by_discipline_splits(db: Session, project, users, ddm_rules):
    mek_rule = DDMRule(
        project_id=project.id, name="MEC", doc_type="DWG", purpose=PurposeOfIssue.IFR, due_days=7
    )
    db.add(mek_rule)
    db.flush()
    db.add(DDMRuleRecipient(rule_id=mek_rule.id, user_id=users["reviewer"].id, action=ActionRequired.APPROVE))
    db.commit()

    revisions = [
        make_revision(db, project, "TST-CIV-DR-001"),
        make_revision(db, project, "TST-MEC-DR-001"),
    ]
    settings = merge_settings({"grouping_strategy": GroupingStrategy.BY_DISCIPLINE.value})
    plans = transmittal_service.plan_groups(
        db, project=project, revisions=revisions, purpose=PurposeOfIssue.IFR, settings_override=settings
    )
    assert len(plans) == 2
    assert {plan.discipline_code for plan in plans} == {"CIV", "MEC"}


def test_grouping_one_per_session(db: Session, project, users, ddm_rules):
    revisions = [
        make_revision(db, project, "TST-CIV-DR-001"),
        make_revision(db, project, "TST-MEC-DR-001"),
    ]
    settings = merge_settings({"grouping_strategy": "one_per_session"})
    plans = transmittal_service.plan_groups(
        db, project=project, revisions=revisions, purpose=PurposeOfIssue.IFR, settings_override=settings
    )
    assert len(plans) == 1


def test_create_drafts_is_idempotent(db: Session, project, users, ddm_rules):
    revision = make_revision(db, project, "TST-CIV-DR-001")
    first = transmittal_service.create_draft_transmittals(
        db,
        project=project,
        revisions=[revision],
        purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL,
        actor=users["dcc"],
    )
    second = transmittal_service.create_draft_transmittals(
        db,
        project=project,
        revisions=[revision],
        purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL,
        actor=users["dcc"],
    )
    assert [t.id for t in first] == [t.id for t in second]
    assert db.query(Transmittal).filter_by(project_id=project.id).count() == 1


def test_external_transmittal_requires_approval(db: Session, project, users, ddm_rules):
    revision = make_revision(db, project, "TST-CIV-DR-001")
    drafts = transmittal_service.create_draft_transmittals(
        db,
        project=project,
        revisions=[revision],
        purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.EXTERNAL,
        actor=users["dcc"],
    )
    assert str(drafts[0].status) == "pending_approval"


def test_issue_allocates_unique_number_and_marks_revision(db: Session, project, users, ddm_rules):
    revision = make_revision(db, project, "TST-CIV-DR-001")
    draft = transmittal_service.create_draft_transmittals(
        db,
        project=project,
        revisions=[revision],
        purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL,
        actor=users["dcc"],
    )[0]
    issued = transmittal_service.issue_transmittal(
        db, transmittal=draft, actor=users["dcc"], storage=None, generate_pdf=False
    )
    db.commit()
    assert issued.number and issued.number.startswith("TRN-TST-")
    assert str(issued.status) == "issued"
    assert str(revision.status) == "issued"
    assert issued.lines and str(issued.lines[0].status) == "issued"


def test_issue_twice_is_rejected(db: Session, project, users, ddm_rules):
    revision = make_revision(db, project, "TST-CIV-DR-001")
    draft = transmittal_service.create_draft_transmittals(
        db, project=project, revisions=[revision], purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL, actor=users["dcc"],
    )[0]
    transmittal_service.issue_transmittal(db, transmittal=draft, actor=users["dcc"], generate_pdf=False)
    db.commit()
    with pytest.raises(ValueError):
        transmittal_service.issue_transmittal(db, transmittal=draft, actor=users["dcc"], generate_pdf=False)


def test_numbering_sequence_increments(db: Session, project):
    first, seq1 = numbering.allocate(db, project)
    second, seq2 = numbering.allocate(db, project)
    db.commit()
    assert seq2 == seq1 + 1
    assert first != second
    assert db.query(NumberSequence).filter_by(project_id=project.id).count() == 1


def test_numbering_format_is_configurable(db: Session, project):
    project.settings = merge_settings({"transmittal_number_format": "T-{project}-{seq:03d}"})
    db.commit()
    number, _ = numbering.allocate(db, project)
    db.commit()
    assert number.startswith("T-TST-")


def test_acknowledge_updates_status(db: Session, project, users, ddm_rules):
    revision = make_revision(db, project, "TST-CIV-DR-001")
    draft = transmittal_service.create_draft_transmittals(
        db, project=project, revisions=[revision], purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL, actor=users["dcc"],
    )[0]
    transmittal_service.issue_transmittal(db, transmittal=draft, actor=users["dcc"], generate_pdf=False)
    db.commit()
    transmittal_service.acknowledge(db, transmittal=draft, user=users["reviewer"])
    db.commit()
    assert str(draft.status) in {"partially_acknowledged", "acknowledged"}


def test_acknowledge_by_non_recipient_fails(db: Session, project, users, ddm_rules):
    revision = make_revision(db, project, "TST-CIV-DR-001")
    draft = transmittal_service.create_draft_transmittals(
        db, project=project, revisions=[revision], purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL, actor=users["dcc"],
    )[0]
    transmittal_service.issue_transmittal(db, transmittal=draft, actor=users["dcc"], generate_pdf=False)
    db.commit()
    with pytest.raises(ValueError):
        transmittal_service.acknowledge(db, transmittal=draft, user=users["submitter"])


def test_discipline_rule_without_purpose_applies_to_every_poi(db, project, users):
    """Regression: a discipline rule pinned to IFR lost its reviewers for an IFC issue."""
    from app.models import DDMRule, DDMRuleRecipient, Discipline, Unit
    from app.models.enums import ActionRequired
    from app.services import ddm

    discipline = db.query(Discipline).filter_by(project_id=project.id, code="CIV").one()
    rule = DDMRule(
        project_id=project.id,
        name="CIV routing",
        discipline_id=discipline.id,
        doc_type="DWG",
        purpose=None,
        default_action=ActionRequired.REVIEW,
        due_days=10,
        priority=10,
    )
    db.add(rule)
    db.flush()
    db.add(DDMRuleRecipient(rule_id=rule.id, user_id=users["reviewer"].id, action=ActionRequired.REVIEW))
    db.commit()

    for purpose in ("IFR", "IFC", "IFE", "IPC"):
        resolved = ddm.resolve(
            db,
            project_id=project.id,
            discipline_id=discipline.id,
            unit_id=None,
            doc_type="DWG",
            purpose=purpose,
            settings=project.settings,
        )
        recipient_ids = {r.user_id for r in resolved.recipients}
        assert users["reviewer"].id in recipient_ids, f"purpose {purpose} lost the reviewer"
