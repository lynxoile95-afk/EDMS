# Persian RAG v2 and file-QA page

- [x] Verify existing 768-dimensional search/index/ACL path and PostgreSQL compose major (`pgvector/pgvector:pg16`).
- [x] Add exact Persian normalisation and 220/45 provenance-aware chunking without changing legacy chunks.
- [x] Add lazy `multilingual-e5-small`/384 embedding profile and additive v2 tables/migration.
- [x] Add DOCX paragraph/table extraction beside the existing PDF/native/OCR path.
- [x] Add resumable/throttled RAG index jobs and admin rebuild endpoints.
- [x] Add authenticated ACL-scoped ask/status/clear/history/document-picker endpoints with active AI Upload runtime.
- [x] Add RTL route/page, conditional menu entry, picker, chat history, source cards, temporary PDF/DOCX input and rebuild control.
- [x] Add SQLite degradation notice and preserve legacy `/search/ask` settings.
- [x] Add normalisation, code preservation, chunking, DOCX, v2 dimension, migration, ACL, source-link and fallback tests.
- [x] Run backend pytest/ruff and frontend typecheck/Vitest/build; note browser/Playwright is unavailable in this sandbox.

## Contract checkpoints

- `normalize_farsi(text)` is the retrieval-only form; `text_original` is displayed.
- `DocumentChunk`/`IndexJob` and `vector(768)` remain unchanged; `DocumentChunkV2` stores nullable `vector(384)` plus model/dimension metadata.
- `FILE_QA_ENABLED=false` is the default; `FILE_QA_MODEL` is the only optional answer-model override.
- Missing grounded context returns `بر اساس مدارک موجود پاسخی یافت نشد.`.
