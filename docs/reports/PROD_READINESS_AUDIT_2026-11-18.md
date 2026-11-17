# Production-readiness audit, 2026-11-18

Scope: functional testing, code and container-level security review. Cloud and hosting items are excluded (see the end).
No credentials or test-account details are recorded here.

## Test results
- Frontend: typecheck, lint, prettier, 68 unit tests, build: pass.
- Backend: 2450 passed, coverage 93.62% (gate 80%).
- Playwright e2e: 26 passed, 2 skipped (manual seed/walkthrough specs), plus the manual seed specs run against a seeded workspace.
- Direct API authorization (`e2e/qa-authz-api.spec.ts`): anonymous 401; viewer and approver write attempts 403; missing CSRF 403; validation 422; duplicate 409; writes persist.
- Direct SQL: counts match the UI, variance total matches ground truth, `audit_log` is append-only, unique/FK constraints reject bad rows, row-level security isolates tenants, the audit hash chain verifies.
- AI pipeline: one synthetic agreement extracted end to end through OpenRouter (16 of 17 fields matched the ground truth); the usage page shows tokens and cost. AI stays off by default.

## Fixed in this change
- Startup now rejects the `replace-me` placeholder in staging/production (`core/config.py`, tested).
- Export and import-error CSV/XLSX formula escaping now also catches payloads that start with spaces.
- Production compose: memory, CPU and PID limits on postgres, seaweedfs, clamav, api and caddy (overridable by env var).
- Dev compose passes `TRUSTED_PROXY_IPS` to the api so e2e per-test client IPs count separately.
- Mobile scroll wrappers on users, adjustments, files and invitations tables.
- Docs: `rebate_lookup` description corrected; `jobs` RLS gap documented.

## Open items
- `jobs` has no RLS: `rebate_app` can read other tenants' job rows including `last_error`. Exact fix in `docs/DATABASE_SCHEMA.md` (needs a separate worker role).
- Backups are unencrypted and stored on the same host.
- `SEAWEEDFS_SSE_KEY` and the `POSTGRES_*` passwords are not validated for placeholders by the app (the database URLs that embed them are).
- Reason-codes page needs a manufacturer chosen first. Left as is on purpose: it is an edit page and an auto-selected manufacturer could be edited by mistake.
- `/files` and `/admin/invitations` still scroll sideways at 390px.
- Not exercised: 1M-row performance, restore drill, retention deletion.
- Pending decisions: hosting and Postgres choice (OD-3, OD-4), AI provider and data terms (OD-2), SMTP provider, off-host backups, TLS/DNS.

## Reviewed, no change needed
- `SECURITY DEFINER` functions pin `search_path`, revoke EXECUTE from PUBLIC and return minimal data.
- The route-access test runs in CI (full pytest, no filters).
