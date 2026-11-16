#!/usr/bin/env bash
# Take the required logical backup before switching postgres:16-alpine to
# pgvector/pgvector:pg16. This script never stops services or removes volumes.
set -euo pipefail

mkdir -p backups
stamp="$(date -u +%Y%m%d-%H%M%S)"
out="backups/edms-before-pgvector-${stamp}.dump"

docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "${out}"
sha256sum "${out}" | tee "${out}.sha256"
printf 'Backup written to %s\n' "${out}"
