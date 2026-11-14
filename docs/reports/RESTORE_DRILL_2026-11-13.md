# Restore drill, 2026-11-13 (local dev stack)

Scope: S11.6, logical backup and restore only. Run unattended by builder L1. No script was changed.
Source: the dev database `rebate` (synthetic data only, 107 tenants, 100 objects, 88,405 bytes) and bucket `rebate-files`, read-only.
Target: throwaway database `rebate_drill` and bucket `rebate-files-drill`. Both were deleted afterwards. The backup lives in the gitignored `backups/` directory of the worktree and is not committed.

## Commands

```sh
export ENV_FILE=.env COMPOSE="docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2" BACKUP_DIR="$PWD/backups"
infra/scripts/ops/backup.sh
infra/scripts/ops/restore.sh backups/<timestamp> rebate_drill rebate-files-drill
```

The scripts default to the production compose file; pointing `COMPOSE` and `ENV_FILE` at the dev stack was enough. `restore.sh` resolved the live names (`rebate`, `rebate-files`) from `compose config` and accepted the fresh names, so no target override was needed.

## Timings

| Step | Duration |
|---|---|
| `backup.sh` (dump, list, objects, sums) | 8 s (9 s wall) |
| Checksums verified | 1 s |
| Create database | 2 s (cumulative) |
| `pg_restore` | 4 s (cumulative) |
| Alembic head check | 8 s (cumulative, includes a one-off container) |
| Object load into the new bucket | 16 s (cumulative) |
| **`restore.sh` total** | **16 s** |

Dataset is tiny (0.9 MB dump, 100 objects of 88 KB). These numbers say nothing about production volume or the 4 h RTO.

## Verification

| Check | Result |
|---|---|
| Row count of every public table (47 tables plus `alembic_version`), source vs restored | identical (largest: audit_log 2232, mfa_recovery_codes 1560, import_staged_rows 847, jobs 706) |
| `alembic_version` restored vs source | both `20261114_0900_calc_runs`, equal to the repo head (script check passed) |
| Tables with row level security enabled | 38 in source and restored |
| `file_objects.sha256` vs SHA-256 of the restored objects in `rebate-files-drill` | 100 ok, 0 mismatch, 0 missing |
| Isolation suite plus engine golden tests (`tests/isolation tests/engine_golden`), `REBATE_TEST_DB=rebate_test_l1` | 380 passed in 34 s |

Caveats, stated plainly:
- The isolation and golden suites ran against a freshly migrated test database (`rebate_test_l1`, since dropped), not against `rebate_drill`. The suite creates its own database, so pointing it at the restored copy was not done. The restored schema was compared by RLS table count and `alembic_version` only. The runbook step "isolation suite against the restored database" is therefore not fully drilled.
- A login against the restored database was not done (no api instance was pointed at it).
- Row counts were compared right after the restore while other agents share the dev stack; they matched, but a concurrent write between dump and compare would have shown as a diff, so equality here is a point-in-time fact.
- `.env` has no `S3_ADMIN_*` identity; the object load used the dev app identity, which can create buckets on SeaweedFS. Production uses a separate admin identity (not exercised here).
- Bucket cleanup needed all object versions and delete markers removed first (versioning is on); done with a one-off boto3 call.

## Not drilled

- WAL archiving and point-in-time recovery: not set up locally and not drilled. The RPO target (15 min) is unproven.
- Encryption and off-host copy of the backup.
- Restore of deleted or overwritten object versions (`objects.tar` holds current versions only).
- Production-sized data, production provider storage.

## Issues found

None in the scripts. Minor: the first-time operator must know to set `ENV_FILE`/`COMPOSE` for a non-production stack; the runbook mentions `COMPOSE` only in the script headers.
