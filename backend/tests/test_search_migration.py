from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import settings


def test_search_migration_round_trip_only_owns_new_objects(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(settings, "database_url", database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url))
    assert {
        "document_chunks",
        "index_jobs",
        "document_chunks_v2",
        "rag_index_jobs",
        "file_qa_sessions",
        "file_qa_messages",
        "source_files",
    }.issubset(set(inspector.get_table_names()))

    command.downgrade(config, "0014_search_indexing")
    inspector = inspect(create_engine(database_url))
    assert "document_chunks_v2" not in inspector.get_table_names()
    assert "rag_index_jobs" not in inspector.get_table_names()
    assert "file_qa_sessions" not in inspector.get_table_names()
    assert "file_qa_messages" not in inspector.get_table_names()
    assert "document_chunks" in inspector.get_table_names()
    assert "index_jobs" in inspector.get_table_names()
    assert "source_files" in inspector.get_table_names()
