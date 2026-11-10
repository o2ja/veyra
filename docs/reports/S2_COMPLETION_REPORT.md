# S2 Completion Report: files, scanning, background jobs

Date: 2026-11-10. Commits `01a1902` to the S2 review-fix commit on `main`.

## Decision summary
1. Job queue: Procrastinate spike not adopted (it cannot enqueue inside our transaction); in-house `SKIP LOCKED` queue per the ADR-005 fallback.
2. Token decision: payloads carry ids only; the email job mints the token, stores its hash and sends it in one transaction.
3. Storage: SeaweedFS 4.50 (digest-pinned) for dev/CI with versioning, SSE-S3 and presigned URLs verified; production provider is still OD-3/OD-4.
4. Two independent reviews: 8 serious findings, all fixed; the minor findings were fixed or documented as K-10..K-12.
5. Owner decisions needed: none.

## What was built
- **Queue (`core/jobs.py`)**: covers the following.
  - Transactional `enqueue` with dedupe keys and ids-only payloads.
  - `SKIP LOCKED` claims with a 10-minute lease.
  - Retries with exponential backoff and jitter, `PermanentError`, and a dead letter with `on_dead` in the same transaction.
  - Lease safety: a job reclaimed after its final attempt is dead-lettered, and a stale worker cannot overwrite a reclaimed job.
  - Per-job `SET LOCAL` tenant context. A tenant-scoped job with no tenant fails before its handler runs.
- **Worker** (`python -m rebate.workers`): role self-check, bucket bootstrap, graceful stop.
- **Files module**:
  - Upload gate, applied before the body is read: session, Origin, session-bound CSRF, 30 uploads/min per user, `Content-Length` required, `Transfer-Encoding` refused, size limit.
  - Validation: extension, declared MIME and magic bytes must agree. XLSX zip-bomb, structure, encryption, duplicate-entry and active-content checks (VBA, XLM, OLE, ActiveX, external links).
  - SHA-256 and duplicate detection.
  - Storage flow: SeaweedFS quarantine, then the `files_scan` job (clamd INSTREAM), then a copy to the clean key; the quarantine copy is purged by a follow-up job after commit.
  - On `infected` or `error`, admins are emailed.
  - Presigned downloads: ≤ 5 min, attachment only, audited, and only for clean files.
  - The DB trigger freezes file identity and final outcomes.
- **K-9 replaced**: all email (registration, reset, email change, invitation, blocked file) is sent by jobs.
- **Jobs admin API and page**: failed jobs, retry (audited).
- **Files UI**: list, upload with progress, scan-status polling, detail page, download.
- **Infra and CI**: Compose services `seaweedfs`, `clamav` (stricter limits plus `AlertExceedsMax`/`AlertEncrypted`/`AlertOLE2Macros`) and `worker`. The CI backend and e2e jobs start real SeaweedFS and ClamAV, plus the worker for e2e.
- **Migrations**: `20261110_1100_files_jobs`, `20261110_1200_s2_review_fixes`.

## How it was tested
- **Backend**: `make test-backend` gave 367 passed, 0 failed, coverage 92.85%. It runs against real Postgres 16, SeaweedFS 4.50, ClamAV 1.4.6 and Mailpit. Covered:
  - files and scanning: EICAR, renamed exe, `.xlsm`, every rejection code, zip bomb, oversize (early 413 and streamed limit), duplicates, quarantine-to-clean copy, versioning, SSE, presigned URL valid then expired (403), scan error notification, re-upload supersede, archive over the clamd limit not clean, clamd `ERROR` reply not clean;
  - jobs: retry/backoff, dead letter, idempotency (dedupe and rescan), worker without tenant context fails, global jobs do not inherit a tenant on a pooled connection, a lost lease is dead-lettered, a stale worker is rejected;
  - DB invariants as `rebate_app`: trigger, CHECKs, no DELETE/TRUNCATE;
  - cross-tenant file and job access through routes and DB (D1/D2/D3/D5/D7/D8/D12/D13 extended).
- **Isolation**: `make test-isolation` passed, and the negative control is OK: with RLS off, 21 RLS-dependent tests detected the breach while the independent tests still passed. Only `api/app.py` changed after that run; it was a swap to `core.db.transaction`, and the full suite was rerun afterwards.
- **Mutation spot-checks**: disabling the lost-lease rule failed the lease tests, and removing the active-part check failed exactly the macrosheet, OLE-embedding and external-link cases. Both were restored, and no MUTATION markers remain.
- **Frontend**: Vitest 7 passed. Playwright 3 passed (W1–W2, axe on S1 and S2 pages including Files and Jobs, and the upload flow: clean then download and detail page; EICAR then blocked). Lint gates (ruff, mypy, import-linter, bandit, pip-audit, ESLint, tsc, npm audit, gitleaks) are all clean.
- **Manual browser (Chrome)**, before the review fixes; e2e was rerun after them:
  1. Registered workspace A and set up MFA.
  2. Uploaded a clean CSV, which showed "Clean" with a Download link.
  3. Uploaded EICAR as an attachment, which showed "Blocked: malware found" with no download.
  4. Stopped ClamAV and uploaded a CSV. The scan failed five times; DB updates skipped the backoff waits. The Jobs page showed "Malware scan 5/5 ScannerError", and the file showed "Scan failed".
  5. Restarted ClamAV and clicked Retry. The job left the failed list and the file became Clean.
  6. Registered workspace B. From B's session, A's file, download-url, job and job retry all returned 404; B's file list was empty and B's job list did not contain A's job.

## Reviewer findings and resolutions
Two fresh-context, read-only reviewers (`.claude/agents/reviewer.md`), one for security and one for the database.

| Finding | Severity | Resolution |
|---|---|---|
| Unauthenticated callers could make the server spool ~51 MB uploads | serious | Fixed: pre-body gate (session, CSRF, Origin, rate limit); `Transfer-Encoding` refused |
| clamd skipped over-limit content and replied OK | serious | Fixed: `AlertExceedsMax`/`AlertEncrypted`/`AlertOLE2Macros`, MaxFileSize/MaxScanSize 400M; test |
| No admin notice on scan `error` | serious | Fixed: `files_notify_blocked` from `on_dead`; test |
| Unbounded re-runs after worker crash; stale worker overwrites | serious (both reviewers) | Fixed: lost-lease dead letter, `CHECK (attempts <= max_attempts)`, attempt-conditional updates; tests |
| Retry after re-upload broke on the unique index and orphaned copies | serious (both) | Fixed: superseded outcome, no copy; test |
| Final file rows still mutable (key, detail) | serious | Fixed: new trigger (final rows frozen, key changes only on promotion); tests |
| No tests for the new DB invariants | serious | Fixed: `test_db_files_jobs.py`, D2/D3/D5/D13 extended |
| Quarantine deleted before commit | minor | Fixed: purge job after commit |
| `[Content_Types].xml` fully inflated | minor | Fixed: bounded read |
| Macro check missed XLM, OLE, ActiveX, external links, duplicate entries | minor | Fixed; tests |
| Non-Latin filenames lost their extension | minor | Fixed; test |
| Missing per-signal tests | minor | Fixed (see above), apart from the items under "Not tested" |
| `on_dead` ran in a separate transaction; reset rows kept forever | minor | Fixed: same transaction plus savepoint; reset `on_dead` deletes the row |
| `rebate_app` had DELETE on `jobs` | minor | Fixed: revoked |
| Dedupe-forever semantics undocumented | minor | Documented (docstring, DATABASE_SCHEMA) |
| Email at-least-once undocumented | minor | Documented (ARCHITECTURE §6, K-12) |
| No nosniff on presigned downloads | minor | Deviation documented (SECURITY §4, K-10); Content-Type now asserted |
| ARCHITECTURE §6 / SECURITY §2.3 stale; `rebate_ai_writer` grant in doc | minor | Docs fixed |
| Worker registry relied on transitive imports | nit | Fixed: explicit imports plus a registry test |
| Hosted S3 config unchecked; app S3 identity has Admin | minor | Hosted rules added (credentials, https). The dev/CI identity keeps Admin (it creates the bucket); production least privilege documented |
| Dead `backend/clamd.conf`, duplicate `LocalSocket`, trigger `search_path` | nit | Fixed |
| `uploaded_by` FK to global `users` | nit | No change: S1 convention, always set from the session |
| UTF-16 CSV rejected | nit | Policy documented: UTF-8/ASCII only |

## Not tested or incomplete
- **SSE ciphertext at rest** could not be independently verified on disk. The tests assert the HEAD `ServerSideEncryption: AES256` and the bucket default-encryption config.
- **Request body length**: there is no in-process byte counter. The body length relies on the server enforcing `Content-Length` (h11/Caddy), with `Transfer-Encoding` refused. The missing-`Content-Length` 411 path has no automated test, because the test client always sends one.
- **Query plans**: no `EXPLAIN` at realistic volume yet; the indexes were matched to the queries by reading them.
- **Lease length**: there is no lease heartbeat. Jobs longer than 10 minutes would be re-run (none exist yet).
- **Retention and alerting**: no retention purge for finished jobs and no external dead-letter alerting (K-11, S9).
- **Manual browser checks** were done before the reviewer fixes; afterwards, only the automated suites and Playwright were rerun.

## Deviations from the docs (all documented)
- Procrastinate replaced by the ADR-005 fallback queue.
- clamd is spoken to over the stdlib socket instead of the `clamd` client.
- Job names: `files_scan` rather than `scan_file`, plus `files_purge_quarantine` and `files_notify_blocked`.
- API additions:
  - `GET /files` list;
  - `GET /jobs` with any `status`;
  - the 411/413/429 pre-body responses.
- Presigned downloads have no `nosniff` (K-10).
- Email delivery is at-least-once (K-12).

## Code review checklist
- **Functionality**: pass. FR-FI-01..04 are covered by tests, and the manual run is recorded above.
- **Architecture**: pass. import-linter is green and the files internals are fenced.
- **Security**: fail, then fixed (reviewer findings above).
- **DB integrity**: fail, then fixed (trigger, CHECK, grants). `EXPLAIN` was not run (see above).
- **Financial**: not applicable (no money in S2).
- **Errors and observability**: pass (standard envelope, `_safe_error`, visible and retryable dead letters).
- **Testing**: pass (real Postgres, SeaweedFS and ClamAV, mutation spot-checks, gaps listed).
- **Performance**: pass for the bounded and paginated lists; no NFR measurement in S2.
- **Frontend**: pass (generated client, permission-aware nav, axe on the new pages).
- **Docs**: pass (ADR-005, ARCHITECTURE, SECURITY, DATABASE_SCHEMA, API, DEPLOYMENT, TECH_STACK, PROJECT_STATUS, plan boxes).
- **Regression**: pass (S1 suites green, migrations apply on top of the S1 head).

## Owner decisions
Nothing required.
