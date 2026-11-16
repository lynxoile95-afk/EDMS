"""PDF review markup workflow regression tests."""

from __future__ import annotations

import hashlib
import uuid

import fitz
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DocumentRevision, MDLItem
from app.services import transmittal_service
from app.services.storage import get_storage
from app.models.enums import PurposeOfIssue, TransmittalType
from tests.conftest import auth_headers


def _pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=300, height=220)
    page.insert_text((30, 50), "Original PDF", fontsize=16)
    payload = document.tobytes()
    document.close()
    return payload


def _draft(client: TestClient, db: Session, project, users):
    item = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    revision = DocumentRevision(
        project_id=project.id,
        mdl_item_id=item.id,
        rev="B",
        rev_seq=2,
        file_hash=uuid.uuid4().hex * 2,
        page_count=1,
        is_current=True,
    )
    db.add(revision)
    db.commit()
    draft = transmittal_service.create_draft_transmittals(
        db,
        project=project,
        revisions=[revision],
        purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL,
        actor=users["dcc"],
    )[0]
    db.commit()
    return draft


def test_markup_permissions_save_reload_submit_outputs_and_original_hash(
    client: TestClient, db: Session, project, users
):
    draft = _draft(client, db, project, users)
    dcc_headers = auth_headers(client, "dcc@edms-test.com")
    submitter_headers = auth_headers(client, "submitter@edms-test.com")
    pdf = _pdf()
    uploaded = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=dcc_headers,
        data={"line_id": str(draft.lines[0].id), "kind": "pdf"},
        files={"file": ("drawing.pdf", pdf, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["attachment"]["id"]
    digest = hashlib.sha256(pdf).hexdigest()

    denied = client.post(
        "/api/v1/markups",
        headers=submitter_headers,
        json={"transmittal_id": str(draft.id), "file_id": file_id},
    )
    assert denied.status_code == 403

    created = client.post(
        "/api/v1/markups",
        headers=dcc_headers,
        json={"transmittal_id": str(draft.id), "file_id": file_id},
    )
    assert created.status_code == 201, created.text
    session = created.json()
    session_id = session["id"]
    assert session["page_count"] == 1
    assert session["original_sha256"] == digest

    saved = client.put(
        f"/api/v1/markups/{session_id}/annotations",
        headers=dcc_headers,
        json={
            "expected_version": 0,
            "annotations": [
                {
                    "kind": "rectangle",
                    "page_no": 1,
                    "points": [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.3}],
                    "text": "ایراد نقشه",
                    "color": "#d64545",
                },
                {
                    "kind": "sticky_note",
                    "page_no": 1,
                    "points": [{"x": 0.5, "y": 0.4}, {"x": 0.8, "y": 0.6}],
                    "text": "لطفاً اصلاح شود",
                    "color": "#e09f3e",
                },
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    assert [item["number"] for item in saved.json()["annotations"]] == [1, 2]
    reloaded = client.get(f"/api/v1/markups/{session_id}", headers=dcc_headers)
    assert reloaded.status_code == 200
    assert len(reloaded.json()["annotations"]) == 2

    submitted = client.post(f"/api/v1/markups/{session_id}/submit", headers=dcc_headers)
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["annotation_count"] == 2
    assert body["session"]["status"] == "submitted"
    assert {item["kind"] for item in body["outputs"]} == {"pdf", "sheet"}
    assert "_COMMENT_" in next(item["filename"] for item in body["outputs"] if item["kind"] == "pdf")

    original = get_storage().get_bytes(draft.meta["attachments"][0]["storage_key"])
    assert hashlib.sha256(original).hexdigest() == digest
    pdf_output = next(item for item in body["outputs"] if item["kind"] == "pdf")
    output = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{pdf_output['attachment_id']}/download",
        headers=dcc_headers,
    )
    assert output.status_code == 200
    assert output.content != pdf
    sheet_output = next(item for item in body["outputs"] if item["kind"] == "sheet")
    sheet = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{sheet_output['attachment_id']}/download",
        headers=dcc_headers,
    )
    assert sheet.status_code == 200
    assert b"Comment" in sheet.content

    locked = client.put(
        f"/api/v1/markups/{session_id}/annotations",
        headers=dcc_headers,
        json={"annotations": []},
    )
    assert locked.status_code == 409

    replied = client.post(
        f"/api/v1/markups/{session_id}/annotations/1/reply",
        headers=dcc_headers,
        json={"text": "بررسی شد", "status": "resolved"},
    )
    assert replied.status_code == 200, replied.text
    assert replied.json()["annotations"][0]["reply_status"] == "resolved"

    detail = client.get(f"/api/v1/transmittals/{draft.id}", headers=dcc_headers)
    assert detail.status_code == 200
    assert sum(item["kind"] == "comment" for item in detail.json()["attachments"]) == 2


def test_markup_admin_delete_is_audited_and_draft_is_author_only(
    client: TestClient, db: Session, project, users
):
    draft = _draft(client, db, project, users)
    headers = auth_headers(client, "dcc@edms-test.com")
    upload = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=headers,
        data={"line_id": str(draft.lines[0].id), "kind": "pdf"},
        files={"file": ("drawing.pdf", _pdf(), "application/pdf")},
    )
    file_id = upload.json()["attachment"]["id"]
    created = client.post(
        "/api/v1/markups",
        headers=headers,
        json={"transmittal_id": str(draft.id), "file_id": file_id},
    )
    session_id = created.json()["id"]
    reviewer = auth_headers(client, "reviewer@edms-test.com")
    forbidden = client.put(
        f"/api/v1/markups/{session_id}/annotations",
        headers=reviewer,
        json={"annotations": []},
    )
    assert forbidden.status_code == 403
    admin = auth_headers(client, "admin@edms-test.com")
    deleted = client.delete(f"/api/v1/markups/{session_id}", headers=admin)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/markups/{session_id}", headers=admin).status_code == 404
