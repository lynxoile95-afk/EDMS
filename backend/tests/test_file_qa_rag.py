from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.models import DocumentChunk, DocumentChunkV2, SourceFile, UploadSession
from app.search.rag_chunking import chunk_blocks
from app.search.text_normalizer import normalize_farsi
from app.search.embeddings import EmbeddingService
from app.search.extraction import extract_docx_blocks
from app.search.rag_index import build_rag_index_file
from app.services.storage import get_storage


def test_persian_normalizer_preserves_engineering_code() -> None:
    value = " كِد ۷۱۰۰-۰۰-EB-EL-SPE-7052 ـ مهندسی\u200cفنی "
    normalized = normalize_farsi(value)
    assert "7100-00-EB-EL-SPE-7052" in normalized
    assert normalize_farsi("7100-00-EB-EL-SPE-7052") == "7100-00-EB-EL-SPE-7052"
    assert "کد" in normalized
    assert "مهندسی فنی" in normalized
    assert "ـ" not in normalized


def test_small_e5_profile_uses_exact_prefixes_and_dimensions() -> None:
    class FakeModel:
        def __init__(self):
            self.calls = []

        def encode(self, values, **kwargs):
            self.calls.append(list(values))
            assert kwargs["normalize_embeddings"] is True
            return [[1.0] + [0.0] * 383 for _ in values]

    model = FakeModel()
    service = EmbeddingService(
        model=model,
        dimensions=384,
        model_name="intfloat/multilingual-e5-small",
        batch_size=16,
    )
    assert len(service.embed_passages(["متن"])[0]) == 384
    assert service.embed_query("سؤال")[0] == 1.0
    assert model.calls == [["passage: متن"], ["query: سؤال"]]


def test_rag_chunks_have_220_words_and_45_word_overlap() -> None:
    words = [f"واژه{i}" for i in range(400)]
    chunks = chunk_blocks([("page:1", " ".join(words))], document_name="sample.pdf")
    assert [len(chunk.text.split()) for chunk in chunks] == [220, 220, 50]
    assert chunks[0].text.split()[-45:] == chunks[1].text.split()[:45]
    assert chunks[1].text.split()[-45:] == chunks[2].text.split()[:45]
    assert chunks[0].source == "page:1"
    assert chunks[0].page_no == 1
    assert chunks[0].source_document == "sample.pdf"


def test_docx_paragraph_and_table_provenance(tmp_path: Path) -> None:
    import docx

    path = tmp_path / "persian.docx"
    document = docx.Document()
    document.add_paragraph("کد 7100-00-EB-EL-SPE-7052")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "عنوان"
    table.cell(0, 1).text = "برق"
    document.save(path)

    blocks = extract_docx_blocks(path)
    assert ("paragraph:1", "کد 7100-00-EB-EL-SPE-7052") in blocks
    assert ("table:1:row:1", "عنوان | برق") in blocks


def test_v2_model_is_additive_and_384_dimensional() -> None:
    legacy_dimension = getattr(DocumentChunk.__table__.c.embedding.type, "dim", getattr(DocumentChunk.__table__.c.embedding.type, "dimensions", None))
    v2_dimension = getattr(DocumentChunkV2.__table__.c.embedding.type, "dim", getattr(DocumentChunkV2.__table__.c.embedding.type, "dimensions", None))
    assert legacy_dimension == 768
    assert v2_dimension == 384
    assert "text_original" in DocumentChunkV2.__table__.c
    assert "text_normalized" in DocumentChunkV2.__table__.c


def test_docx_rag_index_is_additive_and_keeps_display_text(db, project, users, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", True)
    import docx

    path = tmp_path / "persian-index.docx"
    document = docx.Document()
    document.add_paragraph("كِد 7100-00-EB-EL-SPE-7052 و مهندسی فنی")
    document.save(path)
    data = path.read_bytes()
    session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    db.add(session)
    db.flush()
    source = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename=path.name,
        storage_key=path.name,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(data),
        sha256="1" * 64,
        uploaded_by_id=users["dcc"].id,
    )
    db.add(source)
    db.flush()
    get_storage().put_bytes(source.storage_key, data, content_type=source.mime_type)
    result = build_rag_index_file(db, source.id)
    assert result["status"] == "completed"
    row = db.query(DocumentChunkV2).filter_by(file_id=source.id, is_active=True).one()
    assert "كِد" in row.text_original
    assert "کد" in row.text_normalized
    assert "7100-00-EB-EL-SPE-7052" in row.text_normalized


def test_english_pdf_rag_index_keeps_page_provenance(db, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", True)
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "English engineering specification 7100-00-EB-EL-SPE-7052")
    data = document.tobytes()
    document.close()
    session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    db.add(session)
    db.flush()
    source = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="english-spec.pdf",
        storage_key="english-spec.pdf",
        mime_type="application/pdf",
        size_bytes=len(data),
        sha256="4" * 64,
        uploaded_by_id=users["dcc"].id,
    )
    db.add(source)
    db.flush()
    get_storage().put_bytes(source.storage_key, data, content_type="application/pdf")
    result = build_rag_index_file(db, source.id)
    assert result["status"] == "completed"
    row = db.query(DocumentChunkV2).filter_by(file_id=source.id, is_active=True).one()
    assert row.page_no == 1
    assert row.source == "page:1"
    assert "engineering specification" in row.text_original


def test_file_qa_status_keeps_sqlite_page_visible_when_advanced_flag_is_off(client, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", False)
    from tests.conftest import auth_headers

    response = client.get("/api/v1/file-qa/status", headers=auth_headers(client, users["dcc"].email))
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["mode"] == "basic"
    assert response.json()["advanced_supported"] is False


def test_file_qa_sqlite_notice_is_exact_when_enabled(client, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", True)
    from tests.conftest import auth_headers

    response = client.get("/api/v1/file-qa/status", headers=auth_headers(client, users["dcc"].email))
    assert response.status_code == 200
    assert response.json()["message"] == "پرسش و پاسخ پیشرفته فقط با PostgreSQL فعال است"
    assert response.json()["advanced_supported"] is False


def test_file_qa_rebuild_remains_admin_only_on_sqlite(client, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", False)
    from tests.conftest import auth_headers

    response = client.post(
        "/api/v1/file-qa/admin/rebuild",
        headers=auth_headers(client, users["dcc"].email),
        json={"file_ids": [], "project_id": str(project.id), "force": True},
    )
    assert response.status_code == 403


def test_file_qa_sqlite_keyword_search_returns_page_and_highlight(client, db, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", False)
    session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    db.add(session)
    db.flush()
    source = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="7100-00-EB-EL-SPE-7052.pdf",
        storage_key="7100-00-EB-EL-SPE-7052.pdf",
        sha256="5" * 64,
        uploaded_by_id=users["dcc"].id,
    )
    db.add(source)
    db.flush()
    db.add(
        DocumentChunkV2(
            file_id=source.id,
            project_id=project.id,
            index_version="persian-rag-v2",
            chunk_index=0,
            page_no=3,
            text_original="English SPECIFICATION for the electrical heat tracing system",
            text_normalized="english specification for the electrical heat tracing system",
            content_hash="6" * 64,
            source="page:3",
            source_document=source.original_filename,
            embedding_model="intfloat/multilingual-e5-small",
            embedding_dimensions=384,
            is_active=True,
        )
    )
    db.commit()
    from tests.conftest import auth_headers

    response = client.get(
        f"/api/v1/file-qa/search?q=SPECIFICATION&project_id={project.id}",
        headers=auth_headers(client, users["dcc"].email),
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "basic"
    item = response.json()["items"][0]
    assert item["document_code"] == "7100-00-EB-EL-SPE-7052"
    assert item["page_no"] == 3
    assert "<mark>SPECIFICATION</mark>" in item["highlight"]
    assert item["page_url"].endswith("#page=3")


def test_file_qa_empty_answer_is_exact_and_does_not_need_an_ai_key(client, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", True)
    from tests.conftest import auth_headers

    response = client.post(
        "/api/v1/file-qa/ask",
        headers=auth_headers(client, users["dcc"].email),
        json={"question": "در مدارک چه آمده؟", "project_id": str(project.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "بر اساس مدارک موجود پاسخی یافت نشد."
    assert response.json()["sources"] == []


def test_file_qa_returns_original_source_and_authenticated_page_link(client, db, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", True)
    session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    db.add(session)
    db.flush()
    source = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="source.pdf",
        storage_key="source.pdf",
        sha256="2" * 64,
        uploaded_by_id=users["dcc"].id,
    )
    db.add(source)
    db.flush()
    db.add(
        DocumentChunkV2(
            file_id=source.id,
            project_id=project.id,
            index_version="persian-rag-v2",
            chunk_index=0,
            page_no=2,
            text_original="كد 7100-00-EB-EL-SPE-7052",
            text_normalized="کد 7100-00-EB-EL-SPE-7052",
            content_hash="3" * 64,
            source="page:2",
            source_document=source.original_filename,
            embedding_model="intfloat/multilingual-e5-small",
            embedding_dimensions=384,
            is_active=True,
        )
    )
    db.commit()
    monkeypatch.setattr("app.api.routes.file_qa.answer_from_hits", lambda question, hits, db: "پاسخ مستند")
    from tests.conftest import auth_headers

    response = client.post(
        "/api/v1/file-qa/ask",
        headers=auth_headers(client, users["dcc"].email),
        json={"question": "کد چیست؟", "file_ids": [str(source.id)]},
    )
    assert response.status_code == 200, response.text
    item = response.json()["sources"][0]
    assert item["excerpt"] == "كد 7100-00-EB-EL-SPE-7052"
    assert item["page_no"] == 2
    assert item["page_url"].startswith("/api/v1/files/source-files/")
    assert "access_token" not in item["page_url"]


def test_file_qa_selected_document_obeys_project_acl(client, db, project, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "file_qa_enabled", True)
    session = UploadSession(project_id=project.id, created_by_id=users["dcc"].id)
    db.add(session)
    db.flush()
    source = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="restricted.pdf",
        storage_key="restricted.pdf",
        sha256="f" * 64,
        uploaded_by_id=users["dcc"].id,
    )
    db.add(source)
    db.commit()
    outsider = "file-qa-outsider@example.com"
    from app.core.security import hash_password
    from app.models import User
    db.add(User(email=outsider, full_name="outsider", hashed_password=hash_password("secret123")))
    db.commit()
    from tests.conftest import auth_headers

    response = client.post(
        "/api/v1/file-qa/ask",
        headers=auth_headers(client, outsider),
        json={"question": "محرمانه؟", "file_ids": [str(source.id)]},
    )
    assert response.status_code == 404
