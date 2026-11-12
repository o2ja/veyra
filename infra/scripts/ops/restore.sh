#!/usr/bin/env bash
# Restore a backup into a FRESH database and bucket, verify, and report timing (never overwrites a live one).
# Usage: restore.sh [--live] <backup dir> <new database name> <new bucket name>
# Env:   COMPOSE as in backup.sh. Refuses a target database that exists, and (unless --live) any target whose
#        database or bucket name equals the live one (REBATE_DB_NAME / S3_BUCKET in the env file).
#        --live allows the live names but still needs an interactive typed confirmation (no env/flag bypass).
#        Object loading uses the S3_ADMIN_* identity from the env file (the app identity cannot create buckets).
# Steps: verify SHA256SUMS -> create DB -> pg_restore -> alembic_version == repo head -> row counts
#        -> load objects into the new bucket. Point DATABASE_URL / S3_BUCKET at the restored copies only
#        after these checks pass (runbooks/backup-restore.md).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../../.."
live=0; [[ "${1:-}" == "--live" ]] && { live=1; shift; }
dir="${1:?backup dir}"; newdb="${2:?new database name}"; newbucket="${3:?new bucket name}"
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE="${COMPOSE:-docker compose --env-file $ENV_FILE -f infra/docker/compose.prod.yml}"
envval() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1; }
livedb="$(envval REBATE_DB_NAME)"; livedb="${livedb:-rebate}"
livebucket="$(envval S3_BUCKET)"; livebucket="${livebucket:-rebate-files}"
case "$newdb" in postgres|template0|template1) echo "refusing target database '$newdb'" >&2; exit 2;; esac
if [[ "$newdb" == "$livedb" || "$newbucket" == "$livebucket" ]]; then
  [[ $live -eq 1 ]] || { echo "refusing: '$newdb'/'$newbucket' is (or overlaps) the LIVE database/bucket '$livedb'/'$livebucket'; use fresh names, or --live" >&2; exit 2; }
  [[ -t 0 ]] || { echo "--live needs an interactive terminal for confirmation" >&2; exit 2; }
  read -r -p "Restoring into LIVE names ($newdb / $newbucket). Type 'restore into live' to continue: " answer
  [[ "$answer" == "restore into live" ]] || { echo "aborted" >&2; exit 2; }
fi
[[ "$newdb" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "database name must match [a-z][a-z0-9_]*" >&2; exit 2; }
start=$(date +%s)
step() { echo "[$(( $(date +%s) - start ))s] $*"; }

(cd "$dir" && sha256sum --check --strict SHA256SUMS) || { echo "CHECKSUM MISMATCH: backup is corrupt" >&2; exit 3; }
step "checksums verified"

psql() { $COMPOSE exec -T postgres psql -X -U postgres -v ON_ERROR_STOP=1 "$@"; }
exists=$(psql -At -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$newdb'")
[[ -z "$exists" ]] || { echo "database '$newdb' already exists; choose a fresh name" >&2; exit 2; }
psql -d postgres -c "CREATE DATABASE \"$newdb\" OWNER rebate_owner" > /dev/null
step "database $newdb created"

# Roles (rebate_app, ...) come from init-db.sh and are cluster-wide. The dump was taken --no-owner, so
# --role makes rebate_owner own everything; grants and RLS policies are restored from the dump.
$COMPOSE exec -T postgres pg_restore -U postgres --dbname "$newdb" --role rebate_owner --no-owner --exit-on-error < "$dir/db.dump"
step "pg_restore done"

# migration check: the restored schema must be at the repository's single Alembic head
restored=$(psql -At -d "$newdb" -c "SELECT version_num FROM alembic_version" | tr -d '\r')
head=$($COMPOSE run --rm --no-deps -T ${RUN_ARGS:-} api alembic heads | awk '{print $1}' | tr -d '\r')
[[ "$restored" == "$head" ]] || { echo "alembic_version $restored != repo head $head" >&2; exit 4; }
step "alembic_version == head ($head)"

psql -d "$newdb" -c "ANALYZE" > /dev/null
echo "row counts:"
psql -d "$newdb" -At -c "SELECT 'tenants', count(*) FROM tenants UNION ALL SELECT 'users', count(*) FROM users UNION ALL SELECT 'file_objects', count(*) FROM file_objects"

admin_id="${S3_ADMIN_ACCESS_KEY_ID:-$(envval S3_ADMIN_ACCESS_KEY_ID)}"
admin_secret="${S3_ADMIN_SECRET_ACCESS_KEY:-$(envval S3_ADMIN_SECRET_ACCESS_KEY)}"
admin_args=(); [[ -z "$admin_id" ]] || admin_args=(-e "S3_ACCESS_KEY_ID=$admin_id" -e "S3_SECRET_ACCESS_KEY=$admin_secret")
$COMPOSE run --rm --no-deps -T ${RUN_ARGS:-} "${admin_args[@]}" api python -c "$(cat "$here/s3_tar.py")" load "$newbucket" < "$dir/objects.tar" \
  || { echo "object load failed" >&2; exit 5; }
step "objects loaded into $newbucket"
echo "RESTORE OK in $(( $(date +%s) - start ))s (database $newdb, bucket $newbucket)"
