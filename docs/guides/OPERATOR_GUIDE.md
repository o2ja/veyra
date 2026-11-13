# Operator Guide

For the person who runs a Veyra Recover installation. Status on `main` (2026-11-13): the production setup below is **written and validated with `docker compose config` in CI, but never run on a real host** (no production host exists yet). Backup and restore were run on the dev stack only. Anything marked "not in Release 1 yet" is not built.

Sources: `docs/DEPLOYMENT.md`, `infra/runbooks/`, `infra/docker/compose.prod.yml`, `.env.prod.example`, `backend/src/rebate/core/config.py`, `backend/src/rebate/core/jobs.py`.

## 1. What runs

One Docker host, Compose project from `infra/docker/compose.prod.yml`: `postgres`, `seaweedfs` (interim object store; production is planned to use a managed S3 bucket, see `docs/PRODUCTION_PLAN.md`), `clamav` (malware scan), `api`, `worker`, `caddy` (TLS, security headers, serves the web app), and a one-off `migrate` service (profile `ops`). Only Caddy is exposed. The host is meant to sit behind Cloudflare (`infra/runbooks/cloudflare-ip-update.md`).

## 2. Deploy

Follow `infra/runbooks/deploy-rollback.md` (first-time host setup, deploy, rollback). In short:

1. `cp .env.prod.example .env.prod`, `chmod 0600`, replace every `replace-me` value with a generated one. Never commit `.env.prod`.
2. Create roles and database once with `infra/scripts/init-db.sh` (the runbook gives the exact command).
3. Provision the bucket with the separate S3 admin identity (`infra/scripts/ops/s3_tar.py init`).
4. `python3 infra/scripts/check-proxy-config.py .env.prod infra/docker/compose.prod.yml` must print OK before every `up`.
5. Take a backup, set `REBATE_IMAGE_TAG`, build, run `migrate` (owner credentials, one-off container), `up -d`, run the smoke checks (`/api/v1/health/ready`, a login, all services healthy).
6. Rollback: set the previous image tag and `up -d api worker caddy`. Do not run `alembic downgrade` on production; fix forward or restore a backup.

The API refuses to start if it connects as a superuser, as the table owner, or with a role that bypasses row level security.

## 3. Configuration

All settings are environment variables read by `backend/src/rebate/core/config.py`; the table with every variable is `docs/DEPLOYMENT.md` section 2.4, and the production list is `.env.prod.example`. Points that matter:

- `ENCRYPTION_KEYS` encrypts MFA secrets. Keep an offline copy: losing it locks every MFA user out. The first key is active; the rest only decrypt.
- AI is **off** unless `LLM_PROVIDER`, `LLM_API_KEY` and model names are set **and** a workspace admin turns on "Allow AI document reading" in Settings. Provider terms (OD-2) are not approved yet.
- Staging and production refuse to start without S3 credentials or with a non-https public S3 endpoint.
- Workspace settings (admin, in the app under Settings): business time zone, MFA required, four-eyes required, AI processing, retention years (stored; automatic deletion is not in Release 1 yet).
- Secret rotation: `infra/runbooks/secret-rotation.md`.

## 4. Jobs and dead letters

Email sending, malware scans, imports, calculation runs, extractions and agreement expiry run as queued jobs in the `jobs` table, executed by the `worker` (`core/jobs.py`). A job retries with exponential backoff up to its attempt limit (default 5); a permanent error or an exhausted budget moves it to `failed` (the dead letter).

- Admins see jobs under **Jobs** in the app (`jobs.manage`; API `GET /jobs`, `POST /jobs/{id}/retry`). Read the error, fix the cause (for example SMTP down, ClamAV down), then retry. Retry resets the attempt budget.
- Never edit queue tables by hand.
- Check weekly: failed jobs, backup status, dependency alerts.
- Not in Release 1 yet: alerting on dead letters and purging old finished jobs (known issue K-11 in `docs/PROJECT_STATUS.md`). Until then someone must look at the Jobs page.

## 5. Backups and restore

`infra/scripts/ops/backup.sh` writes `db.dump` (all tenants), `objects.tar` (current object versions) and `SHA256SUMS`. `infra/scripts/ops/restore.sh` restores into a fresh database and bucket, verifies checksums, the Alembic head and row counts, and refuses the live names unless `--live` plus a typed confirmation. Full procedure: `infra/runbooks/backup-restore.md`.

Status:
- Dev-stack drill done and timed 2026-11-13 (backup 8 s, restore 16 s on a small dataset): `docs/reports/RESTORE_DRILL_2026-11-13.md`. A drill on a copy of production-sized data is still required.
- You must schedule the nightly run, alert on failure, encrypt and ship the files off host. The scripts do none of that.
- WAL archiving / point-in-time recovery is **configuration only, never run**. Without it the recovery point is the last nightly dump, not the 15 minutes in the target.
- Per-tenant restore is not in Release 1 yet.
- A backup holds all customers. Treat it as the most sensitive file you own.

Repeat the restore drill monthly and record it in `docs/PROJECT_STATUS.md`.

## 6. Runbooks index (`infra/runbooks/`)

| Situation | Runbook |
|---|---|
| User or sole admin lost MFA | `locked-out-admin.md` (operator SQL: `infra/scripts/ops/mfa-reset.sql`; first admin: `promote-admin.sql`) |
| Deploy, first-time host, rollback | `deploy-rollback.md` |
| Backup, restore, drill, PITR configuration | `backup-restore.md` |
| Rotate any secret | `secret-rotation.md` |
| Suspected cross-tenant data exposure | `incident-tenant-data-exposure.md` |
| Refresh Cloudflare IP ranges | `cloudflare-ip-update.md` |

## 7. Not in Release 1 yet

Monitoring and alert wiring, uptime checks, Sentry setup, per-tenant export or restore, support break-glass access tooling, and the audit log hash-chain seal (planned in S11). Pilot readiness checklist: `docs/DEPLOYMENT.md` section 9.
