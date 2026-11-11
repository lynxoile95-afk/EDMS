"""Authenticated, same-origin previews for real transmittal attachments."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DocumentRevision, MDLItem
from app.models.enums import PurposeOfIssue, TransmittalType
from app.services import transmittal_service
from tests.conftest import auth_headers


FAKE_PDF = b"%PDF-1.4\n% attachment preview fixture\n%%EOF\n"


def _draft(client: TestClient, db: Session, project, users, actor_key: str = "dcc"):
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
        actor=users[actor_key],
    )[0]
    db.commit()
    return draft


def test_pdf_and_native_attachment_previews_are_authenticated_and_inline(
    client: TestClient, db: Session, project, users
):
    draft = _draft(client, db, project, users)
    headers = auth_headers(client, "dcc@edms-test.com")
    line_id = str(draft.lines[0].id)

    uploaded_pdf = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=headers,
        data={"line_id": line_id, "kind": "pdf"},
        files={"file": ("drawing.pdf", FAKE_PDF, "application/pdf")},
    )
    assert uploaded_pdf.status_code == 201, uploaded_pdf.text
    pdf_id = uploaded_pdf.json()["attachment"]["id"]

    pdf_preview = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{pdf_id}/preview", headers=headers
    )
    assert pdf_preview.status_code == 200, pdf_preview.text
    assert pdf_preview.headers["content-type"].startswith("application/pdf")
    assert pdf_preview.headers["content-disposition"].startswith("inline;")
    assert pdf_preview.content == FAKE_PDF

    uploaded_native = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=headers,
        data={"line_id": line_id, "kind": "native"},
        files={"file": ("model.dwg", b"native-cad-bytes", "application/octet-stream")},
    )
    assert uploaded_native.status_code == 201, uploaded_native.text
    native_id = uploaded_native.json()["attachment"]["id"]

    native_preview = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{native_id}/preview", headers=headers
    )
    assert native_preview.status_code == 200, native_preview.text
    assert native_preview.headers["content-disposition"].startswith("inline;")
    assert native_preview.content == b"native-cad-bytes"
    assert uploaded_native.json()["attachment"]["access_restricted"] is True

    reviewer_headers = auth_headers(client, "reviewer@edms-test.com")
    reviewer_denied = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{native_id}/preview", headers=reviewer_headers
    )
    assert reviewer_denied.status_code == 403

    granted = client.patch(
        f"/api/v1/transmittals/{draft.id}/attachments/{native_id}/access",
        headers=headers,
        json={"user_ids": [str(users["reviewer"].id)]},
    )
    assert granted.status_code == 200, granted.text
    reviewer_allowed = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{native_id}/preview", headers=reviewer_headers
    )
    assert reviewer_allowed.status_code == 200
    assert reviewer_allowed.content == b"native-cad-bytes"

    client.cookies.clear()
    unauthenticated = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{pdf_id}/preview"
    )
    assert unauthenticated.status_code == 401


def test_native_issuer_does_not_need_dcc_role_to_upload(client: TestClient, db: Session, project, users):
    """The Native action follows the transmittal issuer, not a UI-only role list."""
    draft = _draft(client, db, project, users, actor_key="submitter")
    headers = auth_headers(client, "submitter@edms-test.com")
    response = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=headers,
        data={"line_id": str(draft.lines[0].id), "kind": "native"},
        files={"file": ("source.dwg", b"issuer-native-bytes", "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    detail = client.get(f"/api/v1/transmittals/{draft.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["sender_id"] == str(users["submitter"].id)
    assert detail.json()["attachments"][0]["kind"] == "native"
