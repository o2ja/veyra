# PITR: WAL archiving, base backups and the restore drill (S11.6)

Status: **written, never executed.** No production host and no real Postgres were available when this was authored;
every command below is unverified until the owner runs the drill and fills in the results table.
Related: `backup-restore.md` (logical dump/restore, still the independent second copy), `audit-seal-verify.md`.

## What is set up

| Piece | Where |
|---|---|
| `wal_level=replica`, `archive_mode=on`, `archive_timeout=300`, `archive_command` | `postgres` service `command:` in `infra/docker/compose.prod.yml` |
| Archive volume | `wal_archive` (compose project `veyra-recover-prod`, so the Docker volume is `veyra-recover-prod_wal_archive`), mounted at `/wal_archive` |
| Segment copy (atomic, idempotent, never overwrites a different file) | `infra/scripts/ops/wal-archive.sh` |
| Base backup + MANIFEST (WAL start segment, alembic head) | `infra/scripts/ops/pitr-base-backup.sh` |
| Restore into a NEW isolated container | `infra/scripts/ops/pitr-restore.sh` |

Script location follows the existing convention (`infra/scripts/ops/`, same as `backup.sh`/`restore.sh`).
The scripts use only what the pinned `postgres` image and Docker already provide (`pg_basebackup`, `cp`, `tar`).
No new package, service or account is needed to run them.

**If OD-3 chooses managed Postgres:** drop the self-managed part: remove the `command:` block, the `wal_archive`
volume and its mount from the `postgres` service (and the service itself). Use the provider's PITR (all managed
providers ship one), keep the nightly `backup.sh` dump as the independent copy, and run the drill below
section "Compare" against the provider's restore-to-new-instance instead of `pitr-restore.sh`.

## Honest limits (read first)

- The archive volume and `/var/backups/veyra/pitr` are on the production host. They survive a Postgres crash or a bad
  deploy, but NOT loss of the host. Until they are copied off-host to a **separate account/provider**, encrypted,
  this is not disaster recovery. The copy is the owner's account decision (OD-3/OD-4) and is not automated here.
  Suggested: a cron on the host that encrypts and uploads new files from the archive volume and each new base-backup
  directory with the tool the owner picks (e.g. `age` + an S3 client). WAL contains every tenant's data: treat it like `db.dump`.
- The RPO claim ("<= 15 min") holds only while the archive keeps working AND the off-host copy keeps up.
  `archive_timeout=300` bounds the gap to about 5 min plus copy lag; nothing here measures the off-host lag.
- Restores need the WAL **in sequence** from the base backup's start segment; one missing segment stops replay there.
  Never delete archive files by hand (see retention).

## First-time setup on the host (once)

```sh
C="docker compose --env-file .env.prod -f infra/docker/compose.prod.yml"
$C up -d postgres              # restart picks up the new settings (a few seconds of downtime; do it in a window)
$C exec -u root postgres chown postgres:postgres /wal_archive   # new named volume is root-owned
$C exec postgres psql -U postgres -Atc "SHOW archive_mode; SHOW wal_level; SELECT pg_switch_wal()"
sleep 10; $C exec postgres psql -U postgres -xc "SELECT * FROM pg_stat_archiver"   # failed_count 0, last_archived_wal set
$C exec postgres ls -l /wal_archive
```

Monitoring (DEPLOYMENT.md section 6): alert when `pg_stat_archiver.failed_count` increases or `last_archived_time` is
older than 10 min, and when the `wal_archive` volume passes 80 %. A failing archive makes `pg_wal` grow until the disk fills.

Schedule `infra/scripts/ops/pitr-base-backup.sh` nightly (cron/systemd timer, alert on non-zero exit).

Retention (manual, until the owner approves automation): keep 35 daily + 12 monthly base backups. Remove WAL only
older than the start segment of the **oldest base backup you keep** (`start_wal_file` in its MANIFEST):
`docker run --rm -v veyra-recover-prod_wal_archive:/a postgres-image pg_archivecleanup -n /a <start_wal_file>`; check the list,
then rerun without `-n`. The same cleanup must be applied to the off-host copy.

## The drill

Run on the production-like host (or a staging host with the same compose file and **synthetic** data at realistic
volume; the numbers on tiny data prove nothing about RTO). Never against real data without the owner present.
Use a scratch shell variable for every time you need: `date -u +%FT%TZ`.

### 1. Prepare a known state

```sh
C="docker compose --env-file .env.prod -f infra/docker/compose.prod.yml"
T0=$(date -u +%FT%TZ)
infra/scripts/ops/pitr-base-backup.sh | tee /tmp/pitr-base.log      # time 1: base backup duration (printed), note the path
BASE=$(sed -n 's/^pitr base backup OK: \([^ ]*\) .*/\1/p' /tmp/pitr-base.log)
```

Then create marker data (staging/synthetic only), freeze writes, snapshot the live counts, and take the target time.
The counts match the restored copy only if nothing writes between the snapshot and `TARGET`, hence the stopped `api`/`worker`:

```sh
P="$C exec -T postgres psql -U postgres -d rebate"
$P -c "CREATE TABLE IF NOT EXISTS pitr_marker(t timestamptz default now(), note text); INSERT INTO pitr_marker(note) VALUES ('before target')"
$C stop api worker
counts() { for t in $($1 -Atc "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"); do echo "$t $($1 -Atc "SELECT count(*) FROM \"$t\"")"; done; }
counts "$P" > /tmp/counts.live                       # the reference
$P -Atc "SELECT version_num FROM alembic_version" > /tmp/alembic.live
sleep 60; TARGET=$(date -u +"%Y-%m-%d %H:%M:%S+00"); sleep 60
$P -c "INSERT INTO pitr_marker(note) VALUES ('after target: must NOT appear')"
$P -c "SELECT pg_switch_wal()"
```


### 2. Restore into a new instance

```sh
infra/scripts/ops/pitr-restore.sh --dry-run "$BASE" "$TARGET" pitr-drill     # read the plan; it refuses the live names
time infra/scripts/ops/pitr-restore.sh "$BASE" "$TARGET" pitr-drill          # time 2: restore + WAL replay to TARGET
```

The script prints cumulative seconds per step. Time 2 is the whole run; the gap between "base backup extracted" and
"recovery finished" is the WAL replay time. The new instance is `docker exec pitr-drill psql -U postgres -d rebate`,
has no network and cannot reach or archive to the live system.

### 3. Compare (all must hold)

```sh
R="docker exec pitr-drill psql -U postgres -d rebate"
```

| Check | Command | Pass |
|---|---|---|
| Marker | `$R -Atc "SELECT note FROM pitr_marker"` | only `before target` |
| Alembic head | `$R -Atc "SELECT version_num FROM alembic_version"` | equals `alembic_head` in `$BASE/MANIFEST` and the live value |
| Row counts, every table | `counts "$R" > /tmp/counts.restored; diff /tmp/counts.live /tmp/counts.restored` (`R` as above, with `-T`-free `docker exec`) | identical (except `pitr_marker`) |
| Object checksums | list `file_objects` (`id, sha256`) on both; for a sample of at least 20 (all, if fewer), download the object from the bucket and `sha256sum` it | every file hash equals `file_objects.sha256`; no object is missing. WAL restores the database only: the bucket is not restored by PITR, so this proves the DB rows and the bucket agree for the time of the target |
| Audit seal verify | start a throwaway `api` container against the restored DB (below) and run the CLI from `audit-seal-verify.md` for 2 tenants, with `--anchor` from the last off-box `head_hash` | exit code 0 for each |
| `pg_stat_archiver` of the LIVE db | `$C exec postgres psql -U postgres -xc "SELECT * FROM pg_stat_archiver"` | `failed_count` unchanged by the drill |

For the audit check point a one-off api container at the restored instance, which has no network, so give it a temporary one:
`docker network create pitr-drill-net; docker network connect --alias postgres pitr-drill-net pitr-drill`, then
`docker run --rm --network pitr-drill-net --env-file .env.prod -e DATABASE_URL=postgresql+psycopg://rebate_app:<REBATE_APP_PASSWORD from .env.prod>@postgres:5432/rebate rebate-api:<tag> python -m rebate.modules.audit.cli verify --tenant <uuid> --anchor <hash>; echo exit=$?`
(do not paste the password into shared logs). Also run the isolation suite against it as in `backup-restore.md`.

Also run once with `latest` to prove full-archive replay: `pitr-restore.sh "$BASE" latest pitr-drill-latest`
(the marker table then contains both rows), and one negative test: a `TARGET` earlier than the base backup must fail (exit 4), not start.

Clean up when finished: `docker rm -f pitr-drill pitr-drill-latest; docker volume rm pitr-drill_data pitr-drill-latest_data; docker network rm pitr-drill-net`; restart `api worker` on the live stack, and `DROP TABLE pitr_marker` if created on a real-looking staging DB.

### 4. Pass criteria vs docs/DEPLOYMENT.md section 7

| Target | Demonstrate |
|---|---|
| **RPO <= 15 min** | The latest segment in the archive and in the off-host copy is never older than 15 min under load: `now() - last_archived_time` (max over the drill, sample every minute) and the recovery of a TARGET taken 5 to 10 min before a forced crash/stop of the live postgres (`kill`/stop without `pg_switch_wal`): the restored marker rows include everything up to the last archived segment. Record the largest observed gap. |
| **RTO <= 4 h** | Wall time from "decide to restore" to a verified instance with the checks above passing, on production-sized (synthetic) data: base-backup download from off-host + `pitr-restore.sh` + comparisons, plus an estimate for switching `REBATE_DB_NAME`/traffic (`backup-restore.md`) |

### 5. Results (owner fills in)

| Item | Value | Notes |
|---|---|---|
| Date / who / host | | |
| Data size (db, `pg_wal` rate per hour) | | |
| Base backup duration (`pitr-base-backup.sh`) | | |
| Base backup size (base + pg_wal tarballs) | | |
| Restore duration total (`pitr-restore.sh`) | | |
| WAL replay duration (extracted -> recovered) and segments replayed | | |
| Target time / latest recovered tx time (log line `last completed transaction`) | | |
| Marker check | | |
| Alembic head (MANIFEST / live / restored) | | |
| Row counts diff | | |
| Object checksum sample (n, mismatches) | | |
| Audit verify exit codes (tenants) | | |
| Largest archive gap observed (RPO) | | |
| Total time to verified instance (RTO) | | |
| RPO <= 15 min met? RTO <= 4 h met? | | |
| Problems / follow-ups | | |

Record the date and the outcome in `docs/PROJECT_STATUS.md`. Repeat monthly and after any change to the Postgres major version.

## Safety refusals in `pitr-restore.sh`

It is not a recovery-in-place tool; to cut over, use the steps in `backup-restore.md` after the restored instance is verified.
It exits 2 and changes nothing when: the container name is the live postgres container, is `postgres`, or starts with
the compose project name; the derived data volume `<name>_data` is the live data volume or already exists; the name or
timestamp is malformed (a target without time zone is refused); the base backup directory is not a
`pitr-base-backup.sh` output; the archive volume does not exist. Exit 3: checksum mismatch. Exit 4: recovery stopped or
timed out. A repeat with identical arguments after success does nothing. It never deletes a volume or container.
