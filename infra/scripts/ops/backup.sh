#!/usr/bin/env bash
# Logical backup: pg_dump (custom format) + object-store copy + SHA-256 manifest (DEPLOYMENT.md section 7).
# Usage: backup.sh            (run from anywhere on the Docker host; it cds to the repo root)
# Env:   COMPOSE   compose invocation (default: production stack with ENV_FILE=.env.prod)
#        RUN_ARGS extra args for the one-off api container (e.g. "-e S3_BUCKET=other")
#        BACKUP_DIR output root (default ./backups)      DB database name (default rebate)
# Output: $BACKUP_DIR/<UTC timestamp>/{db.dump,objects.tar,SHA256SUMS}. Encrypt and ship off-host afterwards
# (runbooks/backup-restore.md). The dump holds ALL tenants: treat it as the most sensitive file you own.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../../.."
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE="${COMPOSE:-docker compose --env-file $ENV_FILE -f infra/docker/compose.prod.yml}"
DB="${DB:-rebate}"
out="${BACKUP_DIR:-./backups}/$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$out"
trap 'echo "backup FAILED, removing partial $out" >&2; rm -rf "$out"' ERR

start=$(date +%s)
$COMPOSE exec -T postgres pg_dump -U postgres --format=custom --no-owner --dbname "$DB" > "$out/db.dump"
# the dump must be readable: list its table of contents
$COMPOSE exec -T postgres pg_restore --list < "$out/db.dump" > /dev/null
$COMPOSE run --rm --no-deps -T ${RUN_ARGS:-} api python -c "$(cat "$here/s3_tar.py")" dump > "$out/objects.tar"
tar -tf - < "$out/objects.tar" > /dev/null
(cd "$out" && sha256sum db.dump objects.tar > SHA256SUMS)
echo "backup OK: $out ($(( $(date +%s) - start ))s)"
cat "$out/SHA256SUMS"
