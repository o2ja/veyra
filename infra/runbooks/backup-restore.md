# Backup, restore, restore drill and PITR

Targets (DEPLOYMENT.md section 7, initial, owner to confirm): RPO <= 15 min (needs WAL archiving, below), RTO <= 4 h.
Status: scripts and the drill below were **executed on the dev stack**. WAL archiving/PITR is **configuration only**:
no production host exists, so it has never run.

## Nightly logical backup (works today)

On the host, from the repo root, with `.env.prod` present:

```sh
infra/scripts/ops/backup.sh            # writes ./backups/<UTC timestamp>/{db.dump,objects.tar,SHA256SUMS}
```

`db.dump` is `pg_dump --format=custom` of the whole `rebate` database (all tenants); `objects.tar` is every current
object of the bucket. The script lists the dump's contents and the tar to prove both are readable, then writes
SHA-256 sums, and deletes the partial directory on any failure (non-zero exit: alert on it).

Operator steps the script does not do (no accounts exist yet):
1. Schedule it (cron/systemd timer) nightly; alert on a non-zero exit or a missing run.
2. Encrypt and copy off-host to a separate account/provider, e.g. `age -r <public key> -o db.dump.age db.dump`
   (keep the private key offline). Retention: 35 daily, 12 monthly.
3. Object store: the bucket has versioning on; `objects.tar` holds current versions only, so deleted/overwritten
   versions are recovered from the bucket itself, not from this tar.

## Restore (into a FRESH database and bucket; never over the live ones)

```sh
infra/scripts/ops/restore.sh backups/<timestamp> rebate_restored rebate-files-restored
```

It verifies `SHA256SUMS` (exit 3 on mismatch), creates the database, `pg_restore --exit-on-error`, checks that
`alembic_version` equals the repository's Alembic head (exit 4), prints row counts and loads the objects.
Then, before switching traffic:
1. Run the smoke checks: `file_objects.sha256` spot-check against restored objects; the isolation suite pointed at
   the restored database; a login.
2. Edit `DATABASE_URL` (database name) and `S3_BUCKET` in `.env.prod`, `docker compose ... up -d api worker`,
   check `/api/v1/health/ready`.
3. Keep the damaged database for forensics until the incident is closed; drop it only after sign-off.

Roles are cluster-wide: on a brand-new host run `infra/scripts/init-db.sh` first (it creates roles, an empty
database and extensions), then restore into a new database name as above.

## Restore drill (mandatory before the first pilot, then monthly)

Record the date, who, the timing lines and any problem in `docs/PROJECT_STATUS.md`.

Drill executed 2026-11-12 on the dev Docker stack (shared Postgres 16.15, SeaweedFS), throwaway database
`h1_drill_src` migrated to head `20261114_0900_calc_runs`, seeded with 2000 synthetic tenant rows and 25 synthetic
objects (1.28 MB); the live `rebate` database and bucket were not touched. Both throwaway databases and buckets were
deleted afterwards.

| Step (restore times are cumulative from start) | Result |
|---|---|
| `backup.sh` (dump + list + objects + sums) | 5 s |
| Checksum verification | 0 s |
| Create DB (2 s) + `pg_restore` | done at 3 s |
| Alembic head check | done at 7 s (includes starting a one-off container) |
| Object load into new bucket | done at 14 s |
| **Restore total (`restore.sh`)** | **14 s** |
| Row counts (tenants / users / file_objects) | 2000 / 0 / 0, equal to source |
| RLS: tables with ENABLE / FORCE; grants to `rebate_app` | 38 / 38; 147; identical to source |
| Tampered `objects.tar` | `sha256sum --check` fails, restore refuses (exit 3) |

These numbers are for a ~1 MB dataset and say nothing about production volumes: re-run the drill on a copy of
real-sized data (synthetic, at scale) before relying on the 4 h RTO. Not covered by the drill: encryption of the
off-host copy, WAL replay, the isolation suite against the restored database (run it as part of the monthly drill).

## WAL archiving / PITR (configuration to apply on the production Postgres; not executed)

Self-run Postgres (OD-3 option a). Recommended tool: pgBackRest (or WAL-G) to object storage in a separate account.
The compose `postgres` service needs a custom image or a mounted config with pgBackRest installed; decide at host
provisioning. Settings in `postgresql.conf`:

```
wal_level = replica
archive_mode = on
archive_command = 'pgbackrest --stanza=rebate archive-push %p'
archive_timeout = 300          # forces a WAL switch at least every 5 min: bounds RPO (target 15 min)
```

pgBackRest: `repo1-type=s3`, `repo1-cipher-type=aes-256-cbc` (encrypted backups), `repo1-retention-full=12`,
`repo1-retention-diff=35`; weekly full, daily differential; `pgbackrest check` after setup and in monitoring;
alert when `pg_stat_archiver.failed_count` rises or `last_archived_time` is older than 10 min.

Point-in-time restore:
1. Stop `api`/`worker`. Provision clean Postgres 16 (new volume) with the same pgBackRest config.
2. `pgbackrest --stanza=rebate --type=time "--target=2026-01-01 12:00:00+00" --target-action=promote restore`
3. Start Postgres, wait for recovery to finish, then `restore.sh`-style checks: `alembic_version` equals the image's
   head, row counts plausible, isolation suite, login.
4. Point `DATABASE_URL` at it, start `api`, `worker`.

Managed Postgres (OD-3 option b): enable the provider's PITR and keep the nightly `backup.sh` dump as the
independent copy.
