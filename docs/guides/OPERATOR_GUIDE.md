# Operator Guide

For the person who runs a Veyra Recover installation. Status on `main` (2026-11-14): the production setup below is **written and validated with `docker compose config` in CI, but never run on a real host** (no production host exists yet). Logical backup and restore were run on the dev stack only; continuous WAL archiving / PITR scripts are written and syntax-checked, but have never been executed against a real PostgreSQL instance.

Sources: `docs/DEPLOYMENT.md`, `infra/runbooks/`, `infra/docker/compose.prod.yml`, `infra/docker/compose.dev.yml`, `.env.prod.example`, `backend/src/rebate/core/config.py`, `backend/src/rebate/core/jobs.py`, `backend/src/rebate/api/deps.py`, `backend/src/rebate/modules/files/service.py`, `backend/src/rebate/modules/audit/seal.py`.

---

## 1. What runs

The production environment runs on a single Docker host managed via `infra/docker/compose.prod.yml`. Services:
- `caddy`: Ingress reverse proxy, TLS termination (Let's Encrypt / ACME), security headers, static frontend asset hosting (`rebate-frontend`). Only Caddy publishes external host ports (`80` and `443`).
- `api`: FastAPI application running under `gunicorn` with 2 `uvicorn.workers.UvicornWorker` workers. Read-only container filesystem, tmpfs `/tmp`.
- `worker`: Background asynchronous task processor (`python -m rebate.workers`). Read-only container filesystem, tmpfs `/tmp:size=512m`, memory and CPU limits.
- `postgres`: PostgreSQL 16 (`postgres:16.15-trixie`), with continuous WAL archiving enabled for PITR.
- `seaweedfs`: Interim object store for files and attachments (`http://seaweedfs:8333`). Managed S3 is planned under Owner Decision OD-3/OD-4.
- `clamav`: Antivirus malware scanning engine (`clamd` on port `3310`).
- `migrate`: One-off database migration container under profile `ops` (`docker compose --profile ops run --rm migrate`). Connects as `rebate_owner` and exits immediately after running `alembic upgrade head`.

The host is designed to sit behind Cloudflare (`infra/runbooks/cloudflare-ip-update.md`).

---

## 2. Dev vs. Production Compose Differences

The repository maintains two compose configurations: `infra/docker/compose.dev.yml` (local development) and `infra/docker/compose.prod.yml` (production and staging).

| Dimension | Development (`compose.dev.yml`) | Production (`compose.prod.yml`) | Source Reference |
|---|---|---|---|
| **Networking** | Default bridge network; publishes ports `5432` (Postgres), `8000` (API), `5173` (Vite), `1080` (Mailpit), `8333` (SeaweedFS). | Two networks: `internal` (`172.30.1.0/24`, `internal: true`, no internet route) and `edge` (`172.30.0.0/24`, outbound internet). Only Caddy exposes host ports `80` and `443`. | `compose.prod.yml:networks` |
| **Proxy Architecture** | Vite dev server proxies `/api` directly to `api:8000`. | Caddy reverse proxies `/api` to `api:8000` on fixed internal IP `172.30.1.2`, serves static files, enforces CSP and HTTPS. | `compose.prod.yml:caddy` |
| **Security Hardening** | Standard containers, root user permitted, host bind mounts for hot reloading. | `x-hardened` template: `restart: unless-stopped`, `no-new-privileges: true`, `cap_drop: ALL`, `read_only: true` filesystems with bounded `tmpfs`. | `compose.prod.yml:x-hardened` |
| **Worker Isolation** | Uncapped CPU and RAM, shared volumes. | Resource bounds: `mem_limit: 3g`, `cpus: 2`, `pids_limit: 256` to ensure hostile spreadsheets or OCR processing cannot exhaust host memory. | `compose.prod.yml:worker` |
| **API Execution** | `uvicorn --reload` mounted directly to host source code. | Gunicorn with 2 Uvicorn workers (`uvicorn.workers.UvicornWorker`), packaged in production Docker image. | `compose.prod.yml:api` |
| **Database Access** | Single database role, exposed on localhost. | Strict role separation (`rebate_owner`, `rebate_app`, `rebate_ai_writer`, `rebate_readonly`). Unexposed to host. Continuous WAL archiving enabled. | `compose.prod.yml:postgres` |
| **Email Handling** | Mailpit mock SMTP server on port 1025 with web UI on 1080. | Real SMTP over TLS (`SMTP_STARTTLS=true`, port 587) with credentials injected via `.env.prod`. | `compose.prod.yml:worker` |
| **Migrations** | Executed manually or via local CLI. | Isolated `migrate` service (`--profile ops`) using temporary container with `rebate_owner` credentials. | `compose.prod.yml:migrate` |

---

## 3. Configuration & Secrets Management

Configuration settings are loaded via environment variables in `backend/src/rebate/core/config.py` using `pydantic-settings` (`env_ignore_empty=True`).

- **Environment File**: In production, variables must be defined in `.env.prod` (`chmod 0600`), never committed to source control.
- **Database Roles**:
  - `rebate_owner`: DDL execution and migrations only (`DATABASE_URL_OWNER`). Never used by runtime web services.
  - `rebate_app`: Web API and standard workers (`DATABASE_URL`). Enforces PostgreSQL Row-Level Security (RLS) via `SET LOCAL app.tenant_id`. The application refuses to start if connected as table owner or superuser.
  - `rebate_ai_writer`: Dedicated credentials (`DATABASE_URL_AI`) for AI gateway writes (`ai_extractions` table).
- **Critical Keys**:
  - `SECRET_KEY`: Used for session cookie signing, CSRF tokens, and internal HMACs.
  - `ENCRYPTION_KEYS`: Semicolon-separated list of 32-byte hex keys used to encrypt MFA secrets in the database. The first key is active for encryption; subsequent keys are decryption fallbacks for key rotation. Operators **must keep an offline backup** of these keys; losing them permanently locks all MFA users out of the system (`infra/runbooks/locked-out-admin.md`).
- **Secret Rotation**: Runbook procedures are detailed in `infra/runbooks/secret-rotation.md`.

---

## 4. AI Settings & LLM Gateway

The AI extraction gateway (`backend/src/rebate/modules/extraction/`) assists analysts by extracting data from complex agreement PDFs and spreadsheets.

### Configuration (`backend/src/rebate/core/config.py`)

- `AI_PROVIDER`: `anthropic`, `gemini`, or `openrouter` (defaults to `anthropic`).
- **API Keys**: Injected exclusively through environment variables or secret stores:
  - `ANTHROPIC_API_KEY` (Anthropic)
  - `GEMINI_API_KEY` (Google Gemini)
  - `OPENROUTER_API_KEY` (OpenRouter)
  - `DATABASE_URL_AI`: Database connection string for `rebate_ai_writer`.
- `OPENROUTER_DATA_COLLECTION`: Defaults to `"deny"` (`backend/src/rebate/core/config.py:119`). When using OpenRouter, this instructs the router to only select upstream model endpoints that commit to never storing or training on submitted prompt data.
- **Models**: `AI_MODEL` (default: `claude-sonnet-5-5`), optional `AI_MODEL_PASS_B` for secondary validation.
- **Token Pricing**: `AI_PRICE_INPUT_PER_MTOK` (default: `3.00`) and `AI_PRICE_OUTPUT_PER_MTOK` (default: `15.00`) define USD costs per million tokens for budget tracking.

### Operational Guardrails & Cost Caps

- **Per-Workspace Opt-In**: AI is **disabled by default**. Even with valid provider keys configured in `.env.prod`, extractions will fail unless a workspace admin explicitly checks "Allow AI document reading" in Settings (`allow_ai_processing=True` in `tenant_settings`).
- **Daily Cost Cap**: `AI_DAILY_COST_CAP_USD` (default: `$25.00` per tenant per UTC day). Once exceeded, further extractions are blocked until the next UTC day.
- **Daily Token Cap**: `AI_DAILY_TOKEN_CAP` (default: `2,000,000` tokens per tenant per day).
- **Rate Limit**: `AI_REQUESTS_PER_MINUTE` (default: `10` requests/minute per tenant across `POST /document-extractions` and `suggest-mapping`).
- **Concurrency Cap**: `AI_MAX_ACTIVE_EXTRACTIONS` (default: `3` concurrent extractions per tenant).
- **Synthetic Documents Restriction**: Under Owner Decision **OD-2**, third-party LLM terms and data collection agreements have not been legally finalized. **Operators must only process synthetic or test documents through the AI gateway until OD-2 is formally resolved.**

---

## 5. Job Queue, Handlers & Dead Letters

Asynchronous background workloads execute via a database-backed FIFO queue in the `jobs` table (`backend/src/rebate/core/jobs.py`), processed continuously by the `worker` service.

### Registered Job Kinds

Every job handler registers with the `@job` decorator in `backend/src/rebate/core/jobs.py`:

| Job Kind | Module & Source | Tenant Scoped | Max Attempts | Dead-Letter Handler (`on_dead`) | Purpose |
|---|---|---|---|---|---|
| `calculation_run` | `claims/jobs.py:57` | Yes | 5 | `_job_died` | Executes rebate calculation runs chunked inside leases; fails run if exhausted. |
| `extract_agreement` | `extraction/jobs.py:9` | Yes | 3 | `service.on_dead` | Performs AI document extraction with OCR, prompt generation, and verification. |
| `claims_export` | `claims/export_jobs.py:154` | Yes | 3 | None | Generates claim batch export files (`kind="batch"`) or missed-claims reports (`kind="missed"`). |
| `audit_pack` | `reporting/jobs.py:107` | Yes | 3 | None | Generates end-to-end trace audit packs (CSV/XLSX) for a claim batch. |
| `remittance_ingest` | `reconciliation/ingest.py:430` | Yes | 3 | `on_dead` | Parses uploaded remittance files (CSV/XLSX/PDF) into staged remittance lines. |
| `remittance_reconcile` | `reconciliation/matching.py:424` | Yes | 3 | None | Executes automated candidate matching between remittance lines and claim batches. |
| `refresh_summaries` | `reporting/jobs.py:67` | Yes | 5 | `_next_hour` | Rebuilds `summary_claims_monthly` rollups and self-reschedules for the next hour. |
| `recovery_sweep` | `recovery/jobs.py:63` | Yes | 5 | `_sweep_died` | Evaluates recovery case dispute deadlines (14/7/3 days) and dispatches notifications. |
| `recovery_digest` | `recovery/jobs.py:141` | Yes | 5 | None | Sends daily email digest of unread recovery notifications to users. |
| `audit_seal` | `audit/jobs.py:41` | Yes | 5 | `_dead` | Bounded hourly seal of audit entries into SHA-256 hash chains; self-rescheduling. |
| `agreements_expiry_sweep` | `agreements/jobs.py:25` | Yes | 5 | None | Sweeps active agreements past their end date and marks them expired. |
| `files_scan` | `files/jobs.py:77` | Yes | 5 | `_scan_dead` | Submits uploaded files to ClamAV daemon for malware scanning. |
| `files_purge_quarantine` | `files/jobs.py:122` | Yes | 5 | None | Purges quarantined infected files after retention window. |
| `files_notify_blocked` | `files/jobs.py:131` | Yes | 5 | None | Sends notification to uploader when a file is blocked by ClamAV. |
| `import_parse` | `ingestion/jobs.py:12` | Yes | 3 | `service.dead` | Inspects and validates uploaded spreadsheet/CSV import columns. |
| `import_load` | `ingestion/jobs.py:17` | Yes | 5 | `service.dead` | Loads staged sales/purchase import rows into transactional database tables. |
| `identity_registration_email` | `identity/jobs.py:32` | No | 5 | None | Sends workspace verification email to newly registered tenant owner. |
| `identity_password_reset` | `identity/jobs.py:53` | No | 5 | `_drop_reset_request` | Sends password reset token email. |
| `identity_email_change` | `identity/jobs.py:91` | No | 5 | None | Sends email verification token for pending email change. |
| `tenancy_invitation_email` | `tenancy/jobs.py:15` | Yes | 5 | None | Sends user onboarding invitation email with accept token. |

### Retention Dry-Run CLI

Data retention policy enforcement is currently implemented as an operator dry-run CLI:
```sh
docker compose exec api python -m rebate.modules.audit.cli retention-report --tenant <tenant-uuid>
```
This executes `retention_report()` in `backend/src/rebate/modules/audit/seal.py:186`. It queries rows in `sales_document_lines`, `calculation_results`, `claim_batch_lines`, `remittance_lines`, and `audit_log` that are older than the workspace's configured `retention_years` (stored in `tenant_settings`). It outputs a JSON summary of eligible rows and **deletes nothing**.

### Viewing and Retrying Failed / Dead-Letter Jobs

Jobs transition through statuses `queued` -> `running` -> `succeeded` or `failed`. A job that exhausts its `max_attempts` budget moves to status `failed` (the dead-letter state).

1. **Web UI**: Navigate to **Admin > Jobs** in the navigation menu. Requires permission `jobs.manage` (Admin role). The screen lists failed jobs with error traces, attempt counts, and timestamps.
2. **REST API**:
   - List failed jobs: `GET /api/v1/jobs?status=failed&limit=50` (`backend/src/rebate/api/jobs_router.py:51`).
   - Get job details: `GET /api/v1/jobs/{job_id}`.
   - Retry dead-letter job: `POST /api/v1/jobs/{job_id}/retry`. Retrying resets `attempts = 0`, sets `status = "queued"`, clears `last_error`, and logs an audit event (`Action.JOB_RETRIED`).
3. **Database Inspection**: Operators can query the `jobs` table directly with `psql`:
   ```sql
   SELECT id, kind, attempts, max_attempts, left(last_error, 100), created_at, finished_at
   FROM jobs
   WHERE status = 'failed'
   ORDER BY finished_at DESC;
   ```
   *Warning*: Never manually edit rows in `jobs` or change job statuses directly in SQL; always use the API retry endpoint.

---

## 6. Request Throttling & 429 Rate Limits

Every HTTP request is intercepted by the pipeline dependencies in `backend/src/rebate/api/deps.py`. Rate limiting uses a sliding window counter backed by PostgreSQL (`backend/src/rebate/modules/identity/ratelimit.py`).

### Rate Limit Tiers

1. **General Traffic**:
   - Authenticated users: `600` requests per minute (`GENERAL_USER_LIMIT = 600`, key `req:user:{user_id}`).
   - Unauthenticated IP addresses: `120` requests per minute (`GENERAL_IP_LIMIT = 120`, key `req:ip:{ip}`).
2. **Expensive Job Enqueue Routes (`JOB_REQUESTS_PER_MINUTE = 6`)**:
   Routes that trigger heavy background processing enforce a strict per-user throttle of **6 requests per minute per family** (`backend/src/rebate/api/deps.py:73`):
   - **`export` family**:
     - `POST /api/v1/claim-batches/{batch_id}/export`
     - `GET /api/v1/missed-claims` (only when queried with `?format=`, representing an export download)
   - **`audit_pack` family**:
     - `POST /api/v1/reports/claim-batches/{batch_id}/audit-pack`
   - **`reconcile` family**:
     - `POST /api/v1/remittances/{remittance_id}/reconcile`
3. **AI Routes**:
   - `10` requests per minute per workspace across `POST /api/v1/document-extractions` and `POST /api/v1/imports/{import_id}/suggest-mapping`.
4. **Permission Denials**:
   - Audited at most once per minute per user × permission combination to prevent denial-of-service on the audit log.

### What HTTP 429 Means

When a rate limit is exceeded:
- The server raises `RateLimited` (`backend/src/rebate/core/errors.py:149`).
- Returns HTTP Status `429 Too Many Requests`.
- Response JSON body:
  ```json
  {
    "error": {
      "code": "rate_limited",
      "message": "Too many requests. Try again later.",
      "details": [],
      "request_id": "req_..."
    }
  }
  ```
- **Response Header**: `Retry-After: <seconds>` indicating the remaining duration of the sliding 60-second window.
- **Commit Behavior**: `RateLimited.commit = True`. The counter increment is committed to the database even though the request fails, preventing brute-force bypasses. The frontend catches 429 responses and displays a friendly non-blocking countdown notification.

---

## 7. Export Files & Access Permissions

Generated exports (claim spreadsheets, missed claims reports, audit packs) are stored as records in `file_objects` with `kind = "export"` (`backend/src/rebate/modules/files/service.py`).

### Export Permission Matrix

Access to export downloads is governed by `export_permission()` in `backend/src/rebate/modules/files/service.py:145`:

| File Type / Pattern | Required Permission | Allowed Roles | Description |
|---|---|---|---|
| `audit-pack-*` | `audit_pack.export` | Admin, Approver, Viewer | Full trace audit pack workbook containing transaction proofs and cryptographic hashes. |
| `missed-claims-*` | `data.read` | Admin, Approver, Analyst, Viewer | Exported list of unbatched claimable lines. |
| All other exports (e.g. `CB-*`) | `claim_batch.export` | Admin, Approver, Analyst | Formatted claim batch export files generated from export templates. |
| Non-export files (uploads) | `data.read` | Admin, Approver, Analyst, Viewer | Clean uploaded documents (agreements, sales lines, remittances). |

### Security Controls

- **Directory Filtering**: In `files.service.list_files()`, export files for which the user lacks the specific export permission are omitted from query results entirely.
- **Download Gate**: `files.service.download_url()` checks `check_readable()` and verifies `scan_status == "clean"`. If the caller lacks permission, it raises `403 PermissionDenied(needed)`; if infected or unverified, it returns `400 InvalidState`.

---

## 8. Audit Seal Chain & Verification CLI

Introduced in S11.1 (ADR-026, `docs/DATABASE_SCHEMA.md`), the audit sealing engine guarantees tamper-evidence for the append-only `audit_log` table.

### Architecture

- Every workspace has an independent SHA-256 seal chain stored in `audit_seals`.
- The `audit_seal` background job (`backend/src/rebate/modules/audit/jobs.py`) runs hourly per workspace.
- Each run selects unsealed `audit_log` entries older than 5 minutes, computes a SHA-256 hash incorporating the previous seal's `seal_hash`, entry count, timestamp range, and row hashes, and inserts a new seal row.
- If a transaction is actively open when the job runs, the job defers sealing with the warning `audit_seal.deferred` to avoid out-of-order sequence gaps.

### Verification CLI

Operators verify audit log integrity from the production API container (`infra/runbooks/audit-seal-verify.md`):

```sh
docker compose exec api python -m rebate.modules.audit.cli verify --tenant <tenant-uuid> [--anchor <seal_hash>]
```

- `--tenant`: Target workspace UUID.
- `--anchor` (optional but recommended): A known `seal_hash` value previously copied and stored off-box.
- **Verification Output (JSON)**:
  ```json
  {
    "ok": true,
    "seals": 48,
    "head_hash": "a1b2c3d4...",
    "unsealed_entries": 12,
    "failure": null,
    "failed_seal_id": null,
    "anchored": true
  }
  ```
- **Exit Codes**:
  - `0`: Seal chain is unbroken and valid back to the genesis seal (or specified anchor).
  - `1`: Tampering or corruption detected. The first broken seal ID is reported in `failed_seal_id`.

### Operational Security Rule

The hashing algorithm is public and keyless. A database superuser or table owner could theoretically modify audit rows and recompute the entire hash chain. **Integrity is mathematically proven only back to the newest seal hash stored off-box.** Operators should schedule a monthly task to read `head_hash` via the verify CLI and archive it in an independent, write-once storage location (e.g. AWS S3 Object Lock or separate cloud account).

---

## 9. Backups, Restore Drills & Point-In-Time Recovery (PITR)

### Logical Backup & Restore

- **Scripts**: `infra/scripts/ops/backup.sh` and `restore.sh`.
- **Artifacts**: Produces `db.dump` (PostgreSQL custom format dump covering all tenants), `objects.tar` (S3 object store snapshot), and `SHA256SUMS`.
- **Status**: The logical restore drill was executed on a dev stack on 2026-11-13 (backup: 8 seconds, restore: 16 seconds on a small seed dataset; `docs/reports/RESTORE_DRILL_2026-11-13.md`). **A restore drill on a production-sized dataset has not yet been executed.**

### Continuous WAL Archiving & PITR

- **Configuration**: Implemented in `infra/docker/compose.prod.yml` on the `postgres` service:
  - `wal_level=replica`
  - `archive_mode=on`
  - `archive_timeout=300` (forces a WAL segment switch every 5 minutes if idle)
  - `archive_command`: Atomically transfers completed 16 MB WAL segments to `/wal_archive` via `infra/scripts/ops/wal-archive.sh`.
- **Base Backups**: `infra/scripts/ops/pitr-base-backup.sh` takes nightly non-exclusive `pg_basebackup` snapshots with metadata manifests.
- **PITR Restore**: `infra/scripts/ops/pitr-restore.sh` provisions a fresh isolated PostgreSQL container, restores the base backup, replays WAL segments up to a target timestamp, and validates row counts and Alembic version.
- **CRITICAL OPERATOR WARNING**: As stated in `infra/runbooks/pitr-restore-drill.md`:  
  > **Status: written, never executed.** No production host and no real Postgres were available when this was authored; every command below is unverified until the owner runs the drill and fills in the results table.  
  The RPO claim (`<= 15 minutes`) and RTO claim (`<= 4 hours`) remain unverified until the operator completes the PITR drill against live PostgreSQL.
- **Off-Host Replication**: The `wal_archive` volume and base backup directories currently reside on the Docker host disk. Off-host transfer to an independent cloud provider is an outstanding operational dependency (Owner Decision OD-3/OD-4).

---

## 10. Database Migrations & Single Head Verification

Migrations are authored with Alembic and stored in `backend/migrations/versions/`.

### Single Head Requirement

The Alembic revision graph must form a strictly linear chain with **exactly one head**. Merging divergent feature branches often creates two heads (e.g. S11 audit seals vs. S7 claim templates), which breaks CI and prevents deployments.

- **Current Migration Head**: `20261121_0900_sdl_superseded_by` (26 total migration revisions).
- **Previous Head**: `20261120_0900_after_write_off`.

### Checking the Migration Chain (CI Method)

In CI and pre-merge checks, the Alembic chain is validated **offline without needing a database connection** using AST/regular-expression parsing of migration files:

```sh
python -I -c "import re, glob; r = {}; [r.update({re.search(r'^revision\s*=\s*[\x22\x27](.+?)[\x22\x27]', open(f, encoding='utf8').read(), re.M).group(1): re.search(r'^down_revision\s*=\s*[\x22\x27]?([^\x22\x27\n]+?)[\x22\x27]?\s*$', open(f, encoding='utf8').read(), re.M).group(1)}) for f in glob.glob('backend/migrations/versions/*.py')]; heads = [x for x in r if x not in r.values()]; print('heads:', heads, 'count:', len(r)); exit(0 if len(heads) == 1 else 1)"
```

Expected output:
```
heads: ['20261121_0900_sdl_superseded_by'] count: 26
```

### Applying Migrations

Never apply migrations from your local workstation or web container. In production, run the dedicated migration container:
```sh
docker compose --env-file .env.prod -f infra/docker/compose.prod.yml --profile ops run --rm migrate
```
This runs `alembic upgrade head && alembic current` using the privileged `rebate_owner` database user.

---

## 11. Logging Standards & Redaction Rules

Application and worker logs are formatted as single-line structured JSON records emitted directly to `sys.stdout` (`backend/src/rebate/core/logging.py`).

### Standard Log Fields

Docker captures stdout using the `json-file` logging driver (configured with `max-size: 20m` and `max-file: 5`):
- `timestamp`: UTC ISO 8601 timestamp.
- `level`: `INFO`, `WARNING`, `ERROR`, `DEBUG`.
- `service`: `rebate.api`, `rebate.workers`, `rebate.jobs`, `rebate.audit_seal`.
- `request_id`: Unique request correlation ID (`req_...`) or background job ID (`job:...`).
- `tenant_id`: Active workspace UUID bound via contextvars.
- `user_id`: Authenticated user UUID.
- `route`: Matched route template (e.g. `/api/v1/claim-batches/{batch_id}`).
- `status`: HTTP response status code.
- `latency_ms`: Execution duration in milliseconds.

### Mandatory Redaction Rules (What Must NEVER Be Logged)

Per `docs/DEPLOYMENT.md §5` and `backend/src/rebate/core/logging.py:3`:
1. **No Request or Response Bodies**: Incoming JSON payloads or multipart form data must never be written to stdout.
2. **No Authentication Secrets**: Passwords, MFA totp secrets, recovery codes, session tokens, or API keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`).
3. **No Raw Email Addresses**: Identity and rate-limiting keys hash email addresses using HMAC (`ratelimit.py:3`).
4. **No Document Contents**: Unencrypted file streams, OCR text, or extracted agreement terms.
5. **No Named Financial Details**: Dollar amounts or rebate percentages tied to identifiable customer or manufacturer names.

The immutable business audit trail is recorded in PostgreSQL (`audit_log`), not in application log files. Ship Docker container logs to an external log aggregator (e.g. Grafana Loki, Papertrail) with a 30-to-90-day retention window.

---

## 12. Runbooks Index (`infra/runbooks/`)

| Runbook | Purpose & Key Commands |
|---|---|
| `deploy-rollback.md` | Deployment, first-time host provisioning, rollback procedures. |
| `backup-restore.md` | Logical backup (`backup.sh`) and restore (`restore.sh`) drills. |
| `pitr-restore-drill.md` | Point-in-time recovery using continuous WAL archive and base backups (*unexecuted on real Postgres*). |
| `audit-seal-verify.md` | Audit seal chain verification CLI and retention report dry-run. |
| `locked-out-admin.md` | Resetting lost MFA for an admin (`mfa-reset.sql`) and promoting first admin (`promote-admin.sql`). |
| `secret-rotation.md` | Rotating `SECRET_KEY`, `ENCRYPTION_KEYS`, database passwords, and API keys. |
| `incident-tenant-data-exposure.md` | Incident response procedure for suspected cross-tenant data leakage. |
| `cloudflare-ip-update.md` | Updating trusted Cloudflare edge IP ranges in Caddy and environment settings. |
