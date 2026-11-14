"""Reply Sheet — the review sheet the discipline engineers annotate.

The Transmittal Workspace has a *Reply Sheet* tab (reference screens 012/013) and the
PDF the customer circulates is built from it. Both must be the same rows: one row per
comment, ordered by document then by registration time, carrying the contractor's
response and the coordinator's disposition.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.models import MDLItem
from tests.conftest import auth_headers
from tests.test_api_flow import _seed_result


def _transmittal_with_comments(client: TestClient, db, project, users, ddm_rules) -> dict:
    """Register a revision, add two comments (one answered), issue the transmittal."""
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    reviewer = auth_headers(client, "reviewer@edms-test.com")
    submitter = auth_headers(client, "submitter@edms-test.com")

    registered = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=dcc,
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "B"},
    )
    assert registered.status_code == 200, registered.text
    revision_id = registered.json()["revision_id"]

    answered = client.post(
        f"/api/v1/revisions/{revision_id}/comments",
        headers=reviewer,
        json={"revision_id": revision_id, "text": "شمال نقشه اشتباه است", "code": "2", "page_no": 1},
    )
    assert answered.status_code == 201, answered.text
    unanswered = client.post(
        f"/api/v1/revisions/{revision_id}/comments",
        headers=reviewer,
        json={"revision_id": revision_id, "text": "مقیاس ذکر نشده", "code": "3", "page_no": 2},
    )
    assert unanswered.status_code == 201, unanswered.text

    reply = client.post(
        f"/api/v1/comments/{answered.json()['id']}/respond",
        headers=submitter,
        json={"response_text": "در Rev C اصلاح شد"},
    )
    assert reply.status_code == 200, reply.text

    created = client.post(
        f"/api/v1/projects/{project.id}/transmittals",
        headers=dcc,
        json={"revision_ids": [revision_id], "purpose": "IFR", "type": "internal"},
    )
    assert created.status_code in (200, 201), created.text
    transmittal = created.json()
    issued = client.post(f"/api/v1/transmittals/{transmittal['id']}/issue", headers=dcc, json={})
    assert issued.status_code == 200, issued.text
    return {"id": transmittal["id"], "revision_id": revision_id}


def test_rows_carry_response_and_disposition(client: TestClient, db, project, users, ddm_rules):
    from app.models import Comment, Transmittal
    from app.services.reply_sheet import reply_sheet_rows

    setup = _transmittal_with_comments(client, db, project, users, ddm_rules)
    transmittal = db.get(Transmittal, uuid.UUID(setup["id"]))
    first = (
        db.query(Comment)
        .filter(Comment.revision_id == uuid.UUID(setup["revision_id"]))
        .order_by(Comment.created_at)
        .first()
    )
    first.disposition = "accepted"
    db.commit()

    rows = reply_sheet_rows(db, transmittal)

    assert len(rows) == 2, "one row per comment, answered or not"
    assert rows[0].doc_code == "TST-CIV-DR-001"
    assert rows[0].response_text == "در Rev C اصلاح شد"
    assert rows[0].disposition == "accepted"
    assert rows[1].response_text is None, "an unanswered comment keeps an empty cell"
    assert rows[0].line_no == 1


def test_transmittal_without_comments_has_no_rows(client: TestClient, db, project, users, ddm_rules):
    from app.models import Comment, Transmittal
    from app.services.reply_sheet import reply_sheet_rows

    setup = _transmittal_with_comments(client, db, project, users, ddm_rules)
    db.query(Comment).filter(
        Comment.revision_id == uuid.UUID(setup["revision_id"])
    ).delete(synchronize_session=False)
    db.commit()
    db.expire_all()

    assert reply_sheet_rows(db, db.get(Transmittal, uuid.UUID(setup["id"]))) == []
