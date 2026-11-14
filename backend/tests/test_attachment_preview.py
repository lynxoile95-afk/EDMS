"""Authenticated, same-origin previews for real transmittal attachments."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DocumentRevision, MDLItem
from app.models.enums import PurposeOfIssue, TransmittalStatus, TransmittalType
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


def test_issuer_can_add_native_to_issued_sheet_but_final_and_cancelled_are_locked(
    client: TestClient, db: Session, project, users
):
    """The Native exception does not reopen PDF attachments or terminal sheets."""
    draft = _draft(client, db, project, users, actor_key="submitter")
    draft.status = TransmittalStatus.ISSUED
    draft.number = "TRN-TST-NATIVE-0001"
    draft.issued_by_id = users["submitter"].id
    db.commit()

    issuer_headers = auth_headers(client, "submitter@edms-test.com")
    native = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=issuer_headers,
        data={"line_id": str(draft.lines[0].id), "kind": "native"},
        files={"file": ("source.dwg", b"issued-native-bytes", "application/octet-stream")},
    )
    assert native.status_code == 201, native.text
    native_id = native.json()["attachment"]["id"]

    non_issuer = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=auth_headers(client, "reviewer@edms-test.com"),
        data={"line_id": str(draft.lines[0].id), "kind": "native"},
        files={"file": ("blocked.dwg", b"blocked-native-bytes", "application/octet-stream")},
    )
    assert non_issuer.status_code == 403

    pdf = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=issuer_headers,
        data={"line_id": str(draft.lines[0].id), "kind": "pdf"},
        files={"file": ("blocked.pdf", FAKE_PDF, "application/pdf")},
    )
    assert pdf.status_code == 409

    granted = client.patch(
        f"/api/v1/transmittals/{draft.id}/attachments/{native_id}/access",
        headers=issuer_headers,
        json={"user_ids": [str(users["reviewer"].id)]},
    )
    assert granted.status_code == 200, granted.text

    draft.status = TransmittalStatus.FINAL
    db.commit()
    final_blocked = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=issuer_headers,
        data={"line_id": str(draft.lines[0].id), "kind": "native"},
        files={"file": ("final.dwg", b"final-native-bytes", "application/octet-stream")},
    )
    assert final_blocked.status_code == 409

    draft.status = TransmittalStatus.CANCELLED
    db.commit()
    cancelled_blocked = client.post(
        f"/api/v1/transmittals/{draft.id}/attachments",
        headers=issuer_headers,
        data={"line_id": str(draft.lines[0].id), "kind": "native"},
        files={"file": ("cancelled.dwg", b"cancelled-native-bytes", "application/octet-stream")},
    )
    assert cancelled_blocked.status_code == 409


def test_related_letter_attachment_is_transmittal_scoped_and_downloadable(
    client: TestClient, db: Session, project, users
):
    draft = _draft(client, db, project, users)
    headers = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/transmittals/{draft.id}/related-attachments",
        headers=headers,
        data={"related_type": "meeting_minutes"},
        files={"file": ("site-minutes.docx", b"meeting-minutes-bytes", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 201, response.text
    attachment = response.json()["attachment"]
    assert attachment["kind"] == "related"
    assert attachment["related_type"] == "meeting_minutes"
    assert attachment["line_id"] is None
    assert attachment["revision_id"] is None

    preview = client.get(
        f"/api/v1/transmittals/{draft.id}/attachments/{attachment['id']}/preview",
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.content == b"meeting-minutes-bytes"
    assert preview.headers["content-disposition"].startswith("inline;")

    detail = client.get(f"/api/v1/transmittals/{draft.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    related = [item for item in detail.json()["attachments"] if item["kind"] == "related"]
    assert len(related) == 1
    assert related[0]["related_type"] == "meeting_minutes"
