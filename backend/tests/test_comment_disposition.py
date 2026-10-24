"""Comment disposition — the vocabulary the reference system prints per comment.

Observed on screen (tag **O** in ``edms-engineering-docs.skill`` →
``references/forms-and-glossary.md``): a comment carries a disposition from
Accepted / Canceled / Commented / No Comment / Rejected. It is a second axis
beside our 1/2/3 comment code, so it gets its own nullable column and is only
written when the coordinator closes the comment.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.models import MDLItem
from tests.test_api_flow import _seed_result


def _comment_ready_to_close(client: TestClient, db, project, users, ddm_rules) -> str:
    """Register a revision, add one comment as reviewer, answer it as submitter."""
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    dcc = _auth(client, "dcc@edms-test.com")
    reviewer = _auth(client, "reviewer@edms-test.com")
    submitter = _auth(client, "submitter@edms-test.com")

    registered = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=dcc,
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "B"},
    )
    revision_id = registered.json()["revision_id"]

    created = client.post(
        f"/api/v1/revisions/{revision_id}/comments",
        headers=reviewer,
        json={"revision_id": revision_id, "text": "کد مدرک با MDL هم‌خوان نیست", "code": "2", "page_no": 1},
    )
    assert created.status_code == 201, created.text
    comment_id = created.json()["id"]

    answered = client.post(
        f"/api/v1/comments/{comment_id}/respond",
        headers=submitter,
        json={"response_text": "در Rev C اصلاح شد"},
    )
    assert answered.status_code == 200, answered.text
    return comment_id


def _auth(client: TestClient, email: str) -> dict[str, str]:
    from tests.conftest import auth_headers

    return auth_headers(client, email)


def test_close_stores_the_disposition(client: TestClient, db, project, users, ddm_rules):
    comment_id = _comment_ready_to_close(client, db, project, users, ddm_rules)
    reviewer = _auth(client, "reviewer@edms-test.com")

    closed = client.post(
        f"/api/v1/comments/{comment_id}/close",
        headers=reviewer,
        json={"disposition": "rejected"},
    )
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["status"] == "closed"
    assert body["disposition"] == "rejected"


def test_disposition_defaults_to_accepted_when_omitted(client: TestClient, db, project, users, ddm_rules):
    comment_id = _comment_ready_to_close(client, db, project, users, ddm_rules)
    reviewer = _auth(client, "reviewer@edms-test.com")

    closed = client.post(f"/api/v1/comments/{comment_id}/close", headers=reviewer)
    assert closed.status_code == 200, closed.text
    assert closed.json()["disposition"] == "accepted"


def test_unknown_disposition_is_rejected_not_stored(client: TestClient, db, project, users, ddm_rules):
    comment_id = _comment_ready_to_close(client, db, project, users, ddm_rules)
    reviewer = _auth(client, "reviewer@edms-test.com")

    for bad in ("Accepted", "ok", ""):
        response = client.post(
            f"/api/v1/comments/{comment_id}/close",
            headers=reviewer,
            json={"disposition": bad},
        )
        assert response.status_code == 422, f"{bad!r} was accepted: {response.text}"


def test_unanswered_comment_cannot_be_closed(client: TestClient, db, project, users, ddm_rules):
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    dcc = _auth(client, "dcc@edms-test.com")
    reviewer = _auth(client, "reviewer@edms-test.com")
    registered = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=dcc,
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "B"},
    )
    revision_id = registered.json()["revision_id"]
    created = client.post(
        f"/api/v1/revisions/{revision_id}/comments",
        headers=reviewer,
        json={"revision_id": revision_id, "text": "نظر بدون پاسخ", "code": "3"},
    )
    comment_id = created.json()["id"]

    response = client.post(
        f"/api/v1/comments/{comment_id}/close",
        headers=reviewer,
        json={"disposition": "no_comment"},
    )
    assert response.status_code == 409, response.text


def test_reclosing_does_not_rewrite_the_disposition(client: TestClient, db, project, users, ddm_rules):
    comment_id = _comment_ready_to_close(client, db, project, users, ddm_rules)
    reviewer = _auth(client, "reviewer@edms-test.com")

    first = client.post(
        f"/api/v1/comments/{comment_id}/close", headers=reviewer, json={"disposition": "commented"}
    )
    assert first.status_code == 200
    assert first.json()["disposition"] == "commented"

    second = client.post(
        f"/api/v1/comments/{comment_id}/close", headers=reviewer, json={"disposition": "rejected"}
    )
    assert second.status_code == 200
    assert second.json()["disposition"] == "commented"


def test_disposition_values_are_the_reference_screen_values():
    from app.models.enums import DISPOSITION_VALUES

    assert DISPOSITION_VALUES == ("accepted", "canceled", "commented", "no_comment", "rejected")
