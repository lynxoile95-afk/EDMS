# PDF content indexing and hybrid search

This document describes the additive, opt-in content index introduced by Alembic
revision `0014_search_indexing`.

## Architecture

1. A successful upload commit creates an `index_jobs` row only when
   `SEARCH_INDEXING_ENABLED=true`. The row is committed before a task is sent to
   the existing Celery mechanism. Queue/database failures are isolated from the
   upload response.
2. A search worker downloads the stored PDF, extracts one page at a time with
   PyMuPDF, and uses optional Tesseract `eng+fas` OCR for pages without a useful
   native text layer. OCR rasters are capped by `OCR_MAX_PIXELS`.
3. Each page is normalised and chunked at about 800 characters with about 100
   characters of overlap. Chunks retain page number, native/OCR provenance and
   offsets.
4. PostgreSQL workers lazily load `intfloat/multilingual-e5-base`; passages use
   `passage: ` and queries use `query: ` prefixes. Embeddings are normalised and
   stored as `vector(768)`. A worker batches model calls and caps concurrent
   indexing work.
5. PostgreSQL uses a generated `simple` `tsvector`, `pg_trgm`, and a pgvector
   cosine index. Search takes the top 30 keyword and top 30 semantic candidates
   and merges them with reciprocal rank fusion, `1/(60 + rank)`.
6. Docker runs indexing on the separate `search-worker` Celery service with
   concurrency one; the existing extraction worker keeps its original queues.
7. A new index version is staged with `is_active=false`. The old active version
   is deactivated and the complete new version activated in one transaction.
   Current document revisions are included by default; old revisions are not.

`source_files.id` is UUID in the existing EDMS schema. The new `file_id` columns
are therefore UUID foreign keys; converting them to BIGINT would break the
existing schema and is intentionally not done.

## Configuration

The safe defaults are disabled:

```dotenv
SEARCH_INDEXING_ENABLED=false
LLM_QA_ENABLED=false
EMBEDDING_MODEL=intfloat/multilingual-e5-base
EMBEDDING_CACHE_DIR=/home/edms/.cache/huggingface
EMBEDDING_BATCH_SIZE=32
INDEX_MAX_CONCURRENT_JOBS=1
OCR_MAX_PIXELS=16000000
SEARCH_OCR_ENABLED=true
SEARCH_QUERY_MAX_LENGTH=300
SEARCH_DEFAULT_LIMIT=10
SEARCH_MAX_LIMIT=50
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

The LLM key is server-side only. It is never returned by an endpoint or sent to
the browser. The optional `/api/v1/search/ask` endpoint remains unavailable
unless both `LLM_QA_ENABLED=true` and a server-side LLM URL/model are configured.

## Database/image change safety

The repository originally used `postgres:16-alpine`. The compose file keeps the
same service name, PostgreSQL major version, ports, environment and `pgdata`
volume, and uses `pgvector/pgvector:pg16` so the vector extension is available.
Before changing an existing installation, take a logical backup and retain the
original image value. The repository also includes
`scripts/backup-postgres-before-pgvector.sh`:

```bash
# Do not use -v and do not remove pgdata.
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "backups/edms-before-pgvector-$(date +%Y%m%d-%H%M%S).dump"
sha256sum backups/*.dump

docker compose up -d postgres
# The API applies 0014 during its normal startup; it can also be run explicitly.
docker compose run --rm api alembic upgrade head
```

Rollback is same-major and reversible:

```bash
# First stop only the application services if needed; preserve the database volume.
# Restore the postgres image line to: postgres:16-alpine
docker compose up -d postgres
# If the application must run without the new objects:
docker compose run --rm api alembic downgrade 0013_ai_credentials
```

The `0014` downgrade drops only `document_chunks`, `index_jobs` and their
indexes. It never drops `vector` or `pg_trgm`; those extensions may belong to
other database consumers. A dump restore is available if a full data rollback
is required. Never run `docker compose down -v`, `DROP DATABASE`, `DROP TABLE`
on existing objects, `TRUNCATE`, or delete `pgdata` as part of this change.

## API and backfill

All routes are under `/api/v1`, require the existing JWT and reuse project ACL:

- `GET /search/status` — feature and PostgreSQL-degraded capability status.
- `GET /search?q=...` — permission-filtered hybrid search. `source_type`,
  `project_id`, `page`, `limit`, and `include_old_revisions` are bounded filters.
- `GET /search/files/{file_id}/status` — per-file job/cursor/page failures.
- `POST /search/admin/reindex` or
  `POST /search/admin/reindex/{file_id}` — administrator re-indexing.
- `POST /search/admin/backfill` — bounded cursor-based resumable backfill.
  Repeat with the returned `next_cursor` while `has_more=true`.
- `POST /search/ask` — optional grounded answer endpoint, disabled by default.

Backfill does not make the request process the PDF. It creates durable rows and
queues the same isolated task; a failed page is recorded without discarding the
other pages. Re-running an explicit force reindex creates a new staging version.

## Preview SQLite and limitations

SQLite Preview remains healthy and can be used with
`SEARCH_INDEXING_ENABLED=true` for development. It stores chunks but does not
load the embedding model and uses a bounded, permission-filtered `LIKE` search.
The UI shows a degraded notice. PostgreSQL is required for tsvector, trigram,
pgvector semantic ranking and production-scale search.

The index is intentionally PDF text/OCR only. It does not parse DWG/Revit/IFC,
does not claim OCR accuracy, and cannot search a file until its background job
has reached an active version. A missing embedding model still leaves a
keyword index and reports the semantic warning in job status. Search requests
are length-limited and rate-limited in-process; deployments with multiple API
workers should additionally apply gateway rate limits.

## Manual regression checklist

With the flag off, verify that login, upload, existing extraction/OCR/decision,
MDL, review, transmittals, downloads, provider credentials, consent checkbox and
existing dashboard/project routes behave exactly as before. Then, in a
PostgreSQL/pgvector environment:

- enable `SEARCH_INDEXING_ENABLED`, upload a native-text PDF and confirm upload
  returns without waiting for indexing;
- upload a scanned Persian/English PDF and verify page-level OCR status and
  `eng+fas` fallback;
- search Persian, English and mixed terms; verify highlights, page links and
  keyword/semantic RRF results;
- confirm a member sees only chunks in permitted projects and a non-member sees
  no result or status for that project;
- register a newer document revision and verify old active chunks are excluded;
- stop/restart the worker mid-file, resume the job, and confirm no duplicate
  active chunks;
- run admin backfill in two cursor batches, force reindex one file, and verify
  status/cursor/error reporting;
- run `alembic downgrade 0013_ai_credentials` then `upgrade head` on a disposable
  database and confirm both new objects and pre-existing objects are intact;
- set `LLM_QA_ENABLED=true` only in a controlled environment and verify answers
  cite authorized chunks without exposing the LLM key.
