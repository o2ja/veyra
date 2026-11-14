#!/usr/bin/env bash
# Physical base backup for point-in-time recovery (pg_basebackup) + MANIFEST (DEPLOYMENT.md section 7).
# Usage: pitr-base-backup.sh      (run on the Docker host; it cds to the repo root)
# Env:   COMPOSE   as in backup.sh        ENV_FILE  (default .env.prod)
#        PITR_DIR  output root (default /var/backups/veyra/pitr, outside the checkout)     DB  (default rebate)
# Output: $PITR_DIR/<UTC timestamp>/{base.tar.gz,pg_wal.tar.gz,backup_manifest,MANIFEST,SHA256SUMS}
# MANIFEST records the WAL start segment and the alembic head, which pitr-restore.sh / the drill compare against.
# Needs the archive running (compose.prod.yml sets it up): a base backup alone restores only to its own end.
# It covers the WHOLE cluster (all tenants, roles): as sensitive as db.dump. Encrypt before it leaves the host.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../../.."
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE="${COMPOSE:-docker compose --env-file $ENV_FILE -f infra/docker/compose.prod.yml}"
envval() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1; }
DB="${DB:-$(envval REBATE_DB_NAME)}"; DB="${DB:-rebate}"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="${PITR_DIR:-/var/backups/veyra/pitr}/$ts"
inbox="/backups/pitr-$ts"          # scratch inside the container volume, removed at the end
umask 077
[[ ! -e "$out" ]] || { echo "refusing: $out exists" >&2; exit 2; }
mkdir -p "$out"
cleanup() { $COMPOSE exec -T -u root postgres rm -rf "$inbox" >/dev/null 2>&1 || true; }
trap 'echo "pitr base backup FAILED, removing partial $out" >&2; rm -rf "$out"; cleanup' ERR
psql() { $COMPOSE exec -T postgres psql -X -U postgres -v ON_ERROR_STOP=1 -At "$@"; }

[[ "$(psql -d postgres -c 'SHOW archive_mode' | tr -d '\r')" == "on" ]] || { echo "archive_mode is not on: restart postgres with compose.prod.yml first" >&2; exit 2; }
[[ -n "$(psql -d postgres -c 'SELECT last_archived_time FROM pg_stat_archiver' | tr -d '\r')" ]] || { echo "no WAL segment archived yet: check archive_command (pg_stat_archiver)" >&2; exit 2; }

start=$(date +%s)
$COMPOSE exec -T -u root postgres pg_basebackup -U postgres -D "$inbox" -Ft -z -X stream -c fast \
  --manifest-checksums=SHA256 --label "veyra-pitr-$ts"
dur=$(( $(date +%s) - start ))
$COMPOSE cp "postgres:$inbox/." "$out/"
cleanup
# end the backup's last segment promptly so it is archived (stream mode already put what recovery needs in pg_wal.tar.gz)
psql -d postgres -c "SELECT pg_switch_wal()" > /dev/null

label="$(tar -xzOf "$out/base.tar.gz" backup_label)"
walfile="$(sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]\{24\}\))$/\1/p' <<< "$label")"
lsn="$(sed -n 's/^START WAL LOCATION: \([0-9A-F/]*\) .*/\1/p' <<< "$label")"
[[ -n "$walfile" ]] || { echo "cannot read START WAL LOCATION from backup_label" >&2; exit 1; }
alembic="$(psql -d "$DB" -c 'SELECT version_num FROM alembic_version' | tr -d '\r')"
[[ -n "$alembic" ]] || { echo "cannot read alembic head from $DB" >&2; exit 1; }
{
  echo "created_utc=$ts"
  echo "pg_version=$(psql -d postgres -c 'SHOW server_version' | tr -d '\r')"
  echo "database=$DB"
  echo "start_wal_file=$walfile"
  echo "start_lsn=$lsn"
  echo "alembic_head=$alembic"
  echo "basebackup_seconds=$dur"
} > "$out/MANIFEST"
(cd "$out" && sha256sum base.tar.gz pg_wal.tar.gz backup_manifest MANIFEST > SHA256SUMS)
echo "pitr base backup OK: $out (pg_basebackup ${dur}s, total $(( $(date +%s) - start ))s)"
cat "$out/MANIFEST"
echo "archiver:"; psql -d postgres -F ' ' -c "SELECT archived_count, failed_count, last_archived_wal, last_archived_time FROM pg_stat_archiver"
