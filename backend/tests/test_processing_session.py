"""End-to-end worker test: a real PDF goes through ingest → extract → decide.

The suite has no PDF fixtures on purpose (binary blobs in git age badly), so the
PDF below is assembled by hand: a single A4 page with a text layer carrying the
title-block labels the heuristic extractor looks for. That keeps the test fast,
deterministic and dependency-free while still exercising the *real* pipeline
(PyMuPDF → pdfplumber → matcher → decision engine → persistence).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import ExtractionResult, SourceFile, UploadSession
from app.models.enums import FileStatus, UploadStatus
from app.services.processing import process_session
from app.services.storage import get_storage
from tests.conftest import auth_headers

PAGE_LINES = [
    (40, 520, "Document No: TST-CIV-DR-001"),
    (40, 500, "Title: Plot Plan"),
    (40, 480, "Discipline: CIV"),
    (40, 460, "Unit: U-100"),
    (40, 440, "Revision: B"),
]


def build_text_pdf(lines: list[tuple[int, int, str]]) -> bytes:
    """Return a minimal but valid one-page PDF with the given text lines."""
    content = ["BT", "/F1 12 Tf"]
    for x, y, text in lines:
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content.append(f"1 0 0 1 {x} {y} Tm ({escaped}) Tj")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def test_process_session_extracts_real_pdf(db: Session, project, users) -> None:
    pdf = build_text_pdf(PAGE_LINES)
    key = f"{project.code}/incoming/session-test.pdf"
    get_storage().put_bytes(key, pdf, "application/pdf")

    session = UploadSession(
        project_id=project.id,
        created_by_id=users["submitter"].id,
        purpose="IFR",
        mode="revision",
        transmittal_type="internal",
        status=UploadStatus.COMMITTED,
        stats={},
    )
    db.add(session)
    db.flush()

    source_file = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="TST-CIV-DR-001-RevB.pdf",
        storage_key=key,
        size_bytes=len(pdf),
        sha256=uuid.uuid4().hex * 2,
        status=FileStatus.UPLOADED,
        uploaded_by_id=users["submitter"].id,
    )
    db.add(source_file)
    db.commit()

    summary = process_session(db, session.id)
    db.commit()

    # The regression this test guards: sheets created inside the worker must be
    # visible to the extraction loop (stale ORM collection cache).
    assert summary["sheets"] == 1

    result = db.query(ExtractionResult).one()
    assert result.doc_code == "TST-CIV-DR-001"
    assert result.rev == "B"
    assert result.overall_confidence > 0
    assert result.review_status == "pending"
    assert result.decision in {"GREEN", "YELLOW"}
    assert result.matched_mdl_item_id is not None

    db.refresh(source_file)
    assert source_file.status == FileStatus.PROCESSED
    assert source_file.page_count == 1


def test_process_session_marks_unreadable_file_failed(db: Session, project, users) -> None:
    key = f"{project.code}/incoming/broken.pdf"
    get_storage().put_bytes(key, b"not-a-pdf", "application/pdf")

    session = UploadSession(
        project_id=project.id,
        created_by_id=users["submitter"].id,
        purpose="IFR",
        mode="new_document",
        transmittal_type="internal",
        status=UploadStatus.COMMITTED,
        stats={},
    )
    db.add(session)
    db.flush()
    source_file = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="broken.pdf",
        storage_key=key,
        size_bytes=9,
        sha256=uuid.uuid4().hex * 2,
        status=FileStatus.UPLOADED,
        uploaded_by_id=users["submitter"].id,
    )
    db.add(source_file)
    db.commit()

    summary = process_session(db, session.id)
    db.commit()

    assert summary["sheets"] == 0
    db.refresh(source_file)
    assert source_file.status == FileStatus.FAILED
    assert source_file.error_message
    assert db.query(ExtractionResult).count() == 0


def test_local_download_route_serves_stored_file(client, db: Session, project, users) -> None:
    """Every download in local mode redirects to /files/download/{key}; it must exist."""
    pdf = build_text_pdf(PAGE_LINES)
    key = f"{project.code}/incoming/download-test.pdf"
    get_storage().put_bytes(key, pdf, "application/pdf")

    session = UploadSession(
        project_id=project.id,
        created_by_id=users["submitter"].id,
        purpose="IFR",
        mode="new_document",
        transmittal_type="internal",
        status=UploadStatus.COMMITTED,
        stats={},
    )
    db.add(session)
    db.flush()
    source_file = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="download-test.pdf",
        storage_key=key,
        size_bytes=len(pdf),
        sha256=uuid.uuid4().hex * 2,
        status=FileStatus.UPLOADED,
        uploaded_by_id=users["submitter"].id,
    )
    db.add(source_file)
    db.commit()

    headers = auth_headers(client, "dcc@edms-test.com")
    # TestClient follows the 302 to /files/download/{key}
    response = client.get(f"/api/v1/files/source-files/{source_file.id}", headers=headers)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-1.4")

    # The login above also planted a session cookie, so drop it to test anonymity.
    client.cookies.clear()
    anonymous = client.get(f"/api/v1/files/download/{key}")
    assert anonymous.status_code == 401


def test_process_session_handles_a_file_linked_by_two_sessions(db: Session, project, users) -> None:
    """A re-uploaded file has several rows sharing one hash; processing must not assume one.

    The lookup for `duplicate_of_file_id` used scalar_one_or_none() and blew up with
    'Multiple rows were found when one or none was required', failing the whole session.
    """
    pdf = build_text_pdf(PAGE_LINES)
    key = f"{project.code}/incoming/retry.pdf"
    get_storage().put_bytes(key, pdf, "application/pdf")
    digest = "b" * 64

    sessions = []
    for _ in range(2):
        session = UploadSession(
            project_id=project.id,
            created_by_id=users["submitter"].id,
            purpose="IFR",
            mode="revision",
            transmittal_type="internal",
            status=UploadStatus.COMMITTED,
            stats={},
        )
        db.add(session)
        db.flush()
        db.add(
            SourceFile(
                session_id=session.id,
                project_id=project.id,
                original_filename="TST-CIV-DR-001-RevB.pdf",
                storage_key=key,  # one stored object, two rows
                size_bytes=len(pdf),
                sha256=digest,
                status=FileStatus.UPLOADED,
                uploaded_by_id=users["submitter"].id,
            )
        )
        sessions.append(session)
    db.commit()

    for session in sessions:
        summary = process_session(db, session.id)
        db.commit()
        assert summary["sheets"] == 1

    results = db.query(ExtractionResult).all()
    assert len(results) >= 2
    # the second session's result points at the file it duplicates
    assert any(result.duplicate_of_file_id is not None for result in results)
