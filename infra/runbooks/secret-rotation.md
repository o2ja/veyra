# Secret rotation

Status: **written, not executed**. All secrets live only in `.env.prod` (0600). Rotate on schedule (at least yearly),
on staff change and immediately on suspected leak (then also follow `incident-tenant-data-exposure.md`).
`C` = `docker compose --env-file .env.prod -f infra/docker/compose.prod.yml`. Take a backup before any rotation.

| Secret | Procedure | Effect |
|---|---|---|
| `SECRET_KEY` (CSRF/signing) | Replace the value, `$C up -d api worker`. No overlap mechanism exists. | All outstanding CSRF tokens and signed links become invalid; users reload. Sessions are server-side and survive |
| `ENCRYPTION_KEYS` (MFA secrets) | Put the NEW `kid:key` FIRST in the comma list, keep the old keys listed, `$C up -d api worker`. Run the re-encrypt command (not yet implemented: added with the first real rotation need, DEPLOYMENT.md 2.4); only after it has run may the old key be removed. Never reuse a kid. | New encryptions use the first key; all listed keys decrypt. Removing an old key early locks users out of MFA |
| DB passwords (`REBATE_APP_PASSWORD`, `REBATE_AI_WRITER_PASSWORD`, `REBATE_READONLY_PASSWORD`, `REBATE_OWNER_PASSWORD`) | Edit `.env.prod`; run `init-db.sh` (idempotent, re-syncs role passwords from its environment); `$C up -d api worker`. Brief connection errors while the app restarts | Old passwords stop working at once |
| `POSTGRES_PASSWORD` | `ALTER ROLE postgres PASSWORD ...` via `$C exec postgres psql -U postgres`, then update `.env.prod` (only backups and init-db use it) | |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Update `.env.prod`, `$C up -d seaweedfs api worker` (SeaweedFS rewrites its identity from env at start). Presigned URLs issued before the change stop working (<= 5 min) | |
| `SEAWEEDFS_SSE_KEY` | Do NOT change on a populated store: objects written under the old key become unreadable. A rotation needs a re-encrypting copy into a new store; plan it as a migration | |
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | Generate new key at Google AI Studio / Anthropic Console / OpenRouter (Settings, Keys), update `.env.prod`, `$C up -d api worker`, verify extraction smoke test, revoke old key at provider | Active calls during reload fail and retry; queued jobs pick up the new key |
| SMTP credentials | Rotate at the provider, update `.env.prod`, `$C up -d worker` | |
| Cloudflare / registry / backup-storage credentials (incl. the off-host copy of `wal_archive` and PITR base backups, `pitr-restore-drill.md`; WAL and base backups hold every tenant's data in clear unless encrypted before upload; no new secret is stored in `.env.prod` for PITR) | Rotate at the provider; update the cron/CI that uses them | |

After rotating: smoke tests from `deploy-rollback.md`, then record the date and which secrets in the ops log. If a
secret may have been exposed, also revoke sessions:
`$C exec -T postgres psql -U postgres -d rebate -c "UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL"`.
