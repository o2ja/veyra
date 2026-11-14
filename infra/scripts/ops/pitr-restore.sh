#!/usr/bin/env bash
# Point-in-time restore of a PITR base backup into a NEW, isolated Postgres container + NEW data volume.
# Usage: pitr-restore.sh [--dry-run] <base-backup dir> <target-time|latest> <new-container-name>
#   target-time  timestamptz WITH explicit zone, e.g. "2026-11-14 09:30:00+00" (or 2026-11-14T09:30:00Z)
#   latest       replay every archived WAL segment
# Env: COMPOSE/ENV_FILE as in backup.sh.  WAL_ARCHIVE_SRC  archive volume name or absolute host dir
#      (default veyra-recover-prod_wal_archive).  PG_IMAGE  override the image (default: the compose postgres image).
#      RECOVERY_TIMEOUT seconds to wait for replay (default 3600).
# Safety: never touches the live database. It REFUSES (exit 2) when the name is, or lies in the namespace of,
#   the live container / compose project, or the new data volume `<name>_data` already exists or is the live one.
#   The new container has no network (--network none), archive_mode=off, and the archive is mounted read-only.
#   Re-running with the same arguments after a success is a no-op (exit 0); different arguments on an existing name refuse.
#   --dry-run prints the plan after the same refusal checks and changes nothing.
# Exit: 0 ok, 2 refused/bad input, 3 checksum mismatch, 4 recovery failed or timed out.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../../.."
dry=0; [[ "${1:-}" == "--dry-run" ]] && { dry=1; shift; }
base="${1:?base-backup dir}"; target="${2:?target time or latest}"; name="${3:?new container name}"
die() { echo "refusing: $*" >&2; exit 2; }
ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE="${COMPOSE:-docker compose --env-file $ENV_FILE -f infra/docker/compose.prod.yml}"
archive="${WAL_ARCHIVE_SRC:-veyra-recover-prod_wal_archive}"
cfg="$($COMPOSE config 2>/dev/null)" || { echo "compose config failed" >&2; exit 2; }
project="$(sed -n 's/^name: //p' <<< "$cfg" | head -1 | tr -d '"\r')"; project="${project:-veyra-recover-prod}"

[[ "$name" =~ ^[a-z0-9][a-z0-9_.-]*$ ]] || die "container name must match [a-z0-9][a-z0-9_.-]*"
ts_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}(:?[0-9]{2})?)$'
[[ "$target" == latest || "$target" =~ $ts_re ]] || die "target must be 'latest' or a timestamp WITH time zone (e.g. '2026-11-14 09:30:00+00')"
[[ -f "$base/SHA256SUMS" && -f "$base/MANIFEST" && -f "$base/base.tar.gz" && -f "$base/pg_wal.tar.gz" ]] || die "$base is not a pitr-base-backup.sh output"
base="$(cd "$base" && pwd)"

# live identity: the running compose postgres container and its data volume, plus the compose namespace
livec="$($COMPOSE ps -q postgres 2>/dev/null | head -1 || true)"
livename=""; livevol=""
if [[ -n "$livec" ]]; then
  livename="$(docker inspect -f '{{.Name}}' "$livec" | tr -d '/\r')"
  livevol="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$livec" | tr -d '\r')"
fi
vol="${name}_data"
[[ "$name" != "$livename" && "$name" != postgres && "$name" != "$project"* ]] || die "'$name' is the live container or inside the '$project' compose namespace"
[[ "$vol" != "$livevol" && "$vol" != "${project}_pgdata" ]] || die "data volume '$vol' is the live data volume"
if [[ "$archive" == /* ]]; then [[ -d "$archive" ]] || die "WAL archive dir $archive not found"
else docker volume inspect "$archive" >/dev/null 2>&1 || die "WAL archive volume '$archive' not found (set WAL_ARCHIVE_SRC)"; fi

want="$base|$target"
if docker container inspect "$name" >/dev/null 2>&1; then
  have="$(docker inspect -f '{{index .Config.Labels "veyra.pitr"}}' "$name")"
  [[ "$have" == "$want" ]] || die "container '$name' exists (not from these arguments)"
  echo "already restored as '$name' ($want): nothing to do"; exit 0
fi
if docker volume inspect "$vol" >/dev/null 2>&1; then die "data volume '$vol' already exists; pick a fresh name (nothing is deleted for you)"; fi

image="${PG_IMAGE:-$(sed -n '/^  postgres:/,/^  [a-z]/s/^    image: //p' <<< "$cfg" | head -1 | tr -d '"\r')}"
[[ -n "$image" ]] || die "cannot resolve the postgres image (set PG_IMAGE)"
restore_cmd="cp /wal_archive/%f %p"
if [[ "$target" == latest ]]; then tline=""; goal="replay all archived WAL"; else tline="recovery_target_time = '$target'"; goal="$tline"; fi
cat <<PLAN
PITR restore plan
  base backup  : $base   ($(tr '\n' ' ' < "$base/MANIFEST"))
  target       : $target
  new container: $name   (image $image, --network none, archive_mode=off)
  new volume   : $vol     (created; live volume '${livevol:-unknown}' is not touched)
  WAL archive  : $archive (mounted read-only)
  recovery     : restore_command = '$restore_cmd'; $goal; recovery_target_action = 'promote'
  steps        : verify SHA256SUMS -> create volume -> extract base.tar.gz + pg_wal.tar.gz -> write recovery config + recovery.signal -> start -> wait for promote
PLAN
[[ $dry -eq 0 ]] || { echo "(dry run: nothing changed)"; exit 0; }

(cd "$base" && sha256sum --check --strict SHA256SUMS) || { echo "CHECKSUM MISMATCH: base backup is corrupt" >&2; exit 3; }
start=$(date +%s); step() { echo "[$(( $(date +%s) - start ))s] $*"; }
step "checksums verified"
docker volume create "$vol" > /dev/null
# any failure from here: the half-built volume is left for inspection, never deleted automatically
docker run --rm --network none --user root --entrypoint bash -e TLINE="$tline" -e RCMD="$restore_cmd" \
  -v "$base:/base:ro" -v "$vol:/pgdata" "$image" -euc '
    tar -xzf /base/base.tar.gz -C /pgdata
    mkdir -p /pgdata/pg_wal && tar -xzf /base/pg_wal.tar.gz -C /pgdata/pg_wal
    { echo "restore_command = '"'"'$RCMD'"'"'"; [ -z "$TLINE" ] || echo "$TLINE"; echo "recovery_target_action = '"'"'promote'"'"'"; } >> /pgdata/postgresql.auto.conf
    touch /pgdata/recovery.signal
    chown -R postgres:postgres /pgdata; chmod 700 /pgdata'
step "base backup extracted, recovery configured"
docker run -d --name "$name" --network none --label "veyra.pitr=$want" \
  -v "$vol:/var/lib/postgresql/data" -v "$archive:/wal_archive:ro" "$image" \
  postgres -c archive_mode=off -c wal_level=replica -c hot_standby=on > /dev/null
step "container started, replaying WAL"
r=""
for _ in $(seq 1 "${RECOVERY_TIMEOUT:-3600}"); do
  [[ "$(docker inspect -f '{{.State.Running}}' "$name")" == true ]] || { docker logs --tail 30 "$name" >&2; echo "recovery FAILED: container stopped (target earlier than the backup end, or WAL missing)" >&2; exit 4; }
  r="$(docker exec "$name" psql -X -U postgres -At -d postgres -c 'SELECT pg_is_in_recovery()' 2>/dev/null || true)"
  [[ "$r" == f ]] && break
  sleep 1
done
[[ "$r" == f ]] || { echo "recovery timed out (RECOVERY_TIMEOUT)" >&2; exit 4; }
step "recovery finished, instance promoted"
docker logs "$name" 2>&1 | grep -E "recovery stopping|last completed transaction" || true
echo "alembic_version: $(docker exec "$name" psql -X -U postgres -At -d "$(sed -n 's/^database=//p' "$base/MANIFEST")" -c 'SELECT version_num FROM alembic_version')"
echo "Inspect: docker exec -it $name psql -U postgres -d $(sed -n 's/^database=//p' "$base/MANIFEST")"
echo "Remove when finished (only after the drill/incident is closed): docker rm -f $name && docker volume rm $vol"
