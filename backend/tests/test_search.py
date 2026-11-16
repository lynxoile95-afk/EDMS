from __future__ import annotations

import uuid

from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.models import DocumentChunk, DocumentRevision, MDLItem, SourceFile, UploadSession
from app.models.enums import UploadStatus
from app.search.chunking import PageText, chunk_pages, chunk_text, normalize_search_text
from app.search.embeddings import EmbeddingService
from app.search.indexing import create_index_jobs, run_index_job
from app.search.query import (
    _highlight_html,
    build_keyword_query,
    build_sqlite_keyword_query,
    search_chunks,
)
from app.services.storage import get_storage


def test_e5_prefixes_and_normalises_embeddings() -> None:
    class FakeModel:
        def __init__(self) -> None:
            self.calls = []

        def encode(self, values, **kwargs):
            self.calls.append((values, kwargs))
            return [[1.0] + [0.0] * 767 for _ in values]

    model = FakeModel()
    service = EmbeddingService(model=model)
    passage = service.embed_passages(["متن سند"])[0]
    query = service.embed_query("سؤال")
    assert model.calls[0][0] == ["passage: متن سند"]
    assert model.calls[1][0] == ["query: سؤال"]
    assert model.calls[0][1]["normalize_embeddings"] is True
    assert len(passage) == len(query) == 768
    assert sum(value * value for value in passage) == 1.0


def test_chunking_is_page_local_and_overlaps() -> None:
    text = "بخش اول. " * 120
    chunks = chunk_text(text, max_chars=800, overlap=100)
    assert len(chunks) > 1
    assert all(0 < len(item.content) <= 800 for item in chunks)
    assert chunks[0].page_no == 1
    assert chunks[0].char_end > chunks[1].char_start

    pages = chunk_pages(
        [
            PageText(1, "native text", "native"),
            PageText(2, "متن OCR", "ocr", 0.82),
        ]
    )
    assert {item.page_no for item in pages} == {1, 2}
    assert next(item for item in pages if item.page_no == 2).source_type == "ocr"


def test_mixed_text_normalisation_keeps_persian_zwnj() -> None:
    assert normalize_search_text("  مهندسی\r\n مدارک\u200cفنی  ") == "مهندسی\nمدارک\u200cفنی"


def test_keyword_sql_uses_bound_input_and_simple_fts() -> None:
    malicious = "x' OR 1=1 --"
    statement = build_keyword_query(malicious, project_id=uuid.uuid4())
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "simple" in str(compiled)
    assert malicious not in str(compiled)
    assert "@@" in str(compiled)


def test_sqlite_search_query_is_bounded() -> None:
    statement = build_sqlite_keyword_query("فارسی انگلیسی", project_ids=[])
    compiled = statement.compile()
    assert "content" in str(compiled)
    assert "فارسی انگلیسی" not in str(compiled)


def test_highlight_escapes_markup_and_marks_regex_terms() -> None:
    plain, marked = _highlight_html("<script>alert(1)</script> drawing", "drawing")
    assert "<script>" in plain
    assert "<script>" not in marked
    assert "<mark>drawing</mark>" in marked


def test_current_revision_is_the_default_search_version(db, project, users) -> None:
    mdl = db.query(MDLItem).filter_by(project_id=project.id).first()
    old_session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    new_session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    db.add_all([old_session, new_session])
    db.flush()
    old_file = SourceFile(
        session_id=old_session.id, project_id=project.id, original_filename="old.pdf",
        storage_key="old.pdf", sha256="d" * 64, uploaded_by_id=users["dcc"].id,
    )
    new_file = SourceFile(
        session_id=new_session.id, project_id=project.id, original_filename="new.pdf",
        storage_key="new.pdf", sha256="e" * 64, uploaded_by_id=users["dcc"].id,
    )
    db.add_all([old_file, new_file])
    db.flush()
    old_revision = DocumentRevision(
        project_id=project.id, mdl_item_id=mdl.id, rev="A", rev_seq=1,
        source_file_id=old_file.id, file_hash="f" * 64, is_current=False,
    )
    new_revision = DocumentRevision(
        project_id=project.id, mdl_item_id=mdl.id, rev="B", rev_seq=2,
        source_file_id=new_file.id, file_hash="g" * 64, is_current=True,
    )
    db.add_all([old_revision, new_revision])
    db.flush()
    db.add_all([
        DocumentChunk(
            file_id=old_file.id, project_id=project.id, revision_id=old_revision.id,
            index_version="old", page_no=1, chunk_index=0, content="revision keyword",
            content_hash="h" * 64, is_active=True,
        ),
        DocumentChunk(
            file_id=new_file.id, project_id=project.id, revision_id=new_revision.id,
            index_version="new", page_no=1, chunk_index=0, content="revision keyword",
            content_hash="i" * 64, is_active=True,
        ),
    ])
    db.commit()
    items, _, _ = search_chunks(db, "keyword", project_ids=[project.id])
    assert [item["filename"] for item in items] == ["new.pdf"]


def test_sqlite_index_job_is_page_resumable(db, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "search_indexing_enabled", True)
    import fitz

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "A searchable engineering drawing page")
    data = pdf.tobytes()
    pdf.close()

    session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    db.add(session)
    db.flush()
    source = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="index-me.pdf",
        storage_key="index-me.pdf",
        size_bytes=len(data),
        sha256="c" * 64,
        uploaded_by_id=users["dcc"].id,
    )
    db.add(source)
    db.flush()
    get_storage().put_bytes(source.storage_key, data, content_type="application/pdf")
    jobs = create_index_jobs(db, [source.id], requested_by_id=users["dcc"].id)
    db.commit()

    result = run_index_job(jobs[0].id)
    assert result["status"] == "completed"
    chunks = db.query(DocumentChunk).filter_by(file_id=source.id, is_active=True).all()
    assert chunks and "searchable" in chunks[0].content
    db.refresh(jobs[0])
    assert jobs[0].status == "completed"


def test_search_endpoint_filters_project_access(client, db, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "search_indexing_enabled", True)
    session = UploadSession(
        project_id=project.id,
        created_by_id=users["dcc"].id,
        status=UploadStatus.COMPLETED,
    )
    db.add(session)
    db.flush()
    source = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="access-controlled.pdf",
        storage_key="access-controlled.pdf",
        size_bytes=3,
        sha256="a" * 64,
        uploaded_by_id=users["dcc"].id,
    )
    db.add(source)
    db.flush()
    db.add(
        DocumentChunk(
            file_id=source.id,
            project_id=project.id,
            index_version="test",
            page_no=1,
            chunk_index=0,
            content="محرمانه drawing keyword",
            content_hash="b" * 64,
            is_active=True,
        )
    )
    db.commit()
    from tests.conftest import auth_headers

    allowed = client.get("/api/v1/search?q=keyword", headers=auth_headers(client, users["dcc"].email))
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["items"][0]["filename"] == "access-controlled.pdf"

    outsider_email = "outsider-search@example.com"
    from app.core.security import hash_password
    from app.models import User

    outsider = User(email=outsider_email, full_name="outsider", hashed_password=hash_password("secret123"))
    db.add(outsider)
    db.commit()
    denied = client.get("/api/v1/search?q=keyword", headers=auth_headers(client, outsider_email))
    assert denied.status_code == 200, denied.text
    assert denied.json()["items"] == []
