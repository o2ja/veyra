# Deployment and Operations

> Principles: portable (Docker + standard Postgres + S3 API), no BaaS, simple first (single host), reproducible from the repo. Production hosting decisions (Postgres self-run vs managed, provider, region) are **PROPOSED / OD-3, OD-4**; everything here works for either. Security controls: `SECURITY.md`. Tech choices: `TECH_STACK.md`.

## 1. Environments

| Env | Purpose | Data | Notes |
|---|---|---|---|
| `local` | Development | Synthetic only | Docker Compose, **new empty database created from scratch** |
| `ci` | Automated tests | Synthetic | Postgres service container, ephemeral |
| `staging` | Pre-release verification, demos, ZAP scans | Synthetic only | Mirrors production topology; separate secrets and buckets |
| `production` | Customers | Real | Separate host/network, backups, monitoring |

Real customer data never exists in `local`, `ci`, or `staging`.

## 2. Local development

### 2.1 Prerequisites
Docker + Docker Compose; GNU make and a POSIX shell (on Windows: Git Bash); Python 3.14 with `uv`; Node 24 LTS with npm (ADR-027). Tests and tools run inside the `api`/`frontend` containers, so local Python/Node are only needed for editor tooling. (A native PostgreSQL 16+ install is acceptable instead of the container: use the same `init-db.sh` against it.)

### 2.2 Services (`infra/docker/compose.dev.yml`)

| Service | Image | Ports (host) | Purpose |
|---|---|---|---|
| `postgres` | `postgres:16.15` (pinned by digest) | `${POSTGRES_HOST_PORT:-5432}` | Database (volume `pgdata`). Host port is configurable for machines that already run Postgres |
| `seaweedfs` | `chrislusf/seaweedfs:4.50` (ADR-017), profile `s2` (enabled by the Makefile), pinned by digest; `infra/seaweedfs/start.sh` writes the S3 identity from env; SSE-S3 key from `SEAWEEDFS_SSE_KEY` | 8333 (S3) | S3-compatible storage. Dev/CI only: its filer IAM gRPC is unauthenticated and the SSE key-encryption key is stored on the filer unwrapped (ports not published beyond the Docker network; production uses the OD-3/OD-4 provider) |
| `clamav` | `clamav/clamav:1.4.6` (digest), profile `s2`; signatures ship in the image, `freshclamd` updates daily; `infra/clamav/clamd.conf` = image default + `StreamMaxLength 60M` (uploads up to 50 MB) | 3310 | Malware scanning |
| `mailpit` | `axllent/mailpit:v1.33.1` (digest) | 1025/8025 | Local SMTP + inbox UI |
| `api` | built from `backend/` | 8000 | FastAPI (reload in dev) |
| `worker` | built from `backend/` (`python -m rebate.workers`), profile `s2` | — | Job worker: email, malware scans; creates the bucket (versioning + default SSE) on start |
| `frontend` | built from `frontend/` | 5173 | Vite dev server (proxy `/api` → `api`) |

Convenience commands (`Makefile`): `make env`, `make up`, `make down`, `make db-init`, `make migrate`, `make test`, `make test-isolation`, `make lint`, `make gen-client`, `make check-client`. Later stages add `make seed-synthetic` and `make e2e`. All dev ports bind to `127.0.0.1` only.

### 2.3 Database setup from scratch

1. `make up` starts an empty Postgres (no restore, no import).
2. `infra/scripts/init-db.sh` (run once per new database, as the Postgres superuser):
   - `CREATE ROLE rebate_owner LOGIN PASSWORD …; CREATE ROLE rebate_app LOGIN …; CREATE ROLE rebate_ai_writer LOGIN …; CREATE ROLE rebate_readonly LOGIN …;` (passwords from env; `rebate_app` etc. with `NOBYPASSRLS NOSUPERUSER`).
   - `CREATE DATABASE rebate OWNER rebate_owner;` then `CREATE EXTENSION btree_gist, pg_trgm, citext;`.
   - `REVOKE ALL ON DATABASE rebate FROM PUBLIC;` grant `CONNECT` to the app roles.
3. `make migrate` runs Alembic **as `rebate_owner`**; migrations create schema, grants (default privileges for `rebate_app`), RLS, triggers, reference data.
4. `make seed-synthetic` (optional): loads `testdata/generated` through the real import/agreement services into a dev workspace (never direct SQL inserts that bypass validation).
5. Startup self-check: the API refuses to start if connected as superuser/owner or a role with `BYPASSRLS`.

### 2.4 Environment variables

Defined by `core/config.py` (pydantic-settings). `.env.example` contains placeholders only; real `.env` files are git-ignored.

| Variable | Example (placeholder) | Notes |
|---|---|---|
| `APP_ENV` | `local` / `ci` / `staging` / `production` | |
| `SECRET_KEY` | random 64 bytes | Signs CSRF tokens etc.; rotate with overlap |
| `DATABASE_URL` | `postgresql+psycopg://rebate_app:***@postgres:5432/rebate` | Runtime role |
| `DATABASE_URL_OWNER` | `…rebate_owner…` | Migrations only; not present in the running API container |
| `DATABASE_URL_AI` | `…rebate_ai_writer…` | AI gateway sessions |
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | | SeaweedFS locally |
| `S3_PUBLIC_ENDPOINT_URL` | `http://localhost:8333` | Host the browser uses for presigned URLs (signatures include the host). Staging/production refuse to start without S3 credentials or with a non-https public endpoint. Production credentials: object Read/Write/List on the one bucket only (bucket, versioning and default encryption provisioned out of band); the dev/CI SeaweedFS identity has Admin so the worker can create the bucket |
| `SEAWEEDFS_SSE_KEY` | 64 hex chars | Dev/CI SeaweedFS SSE-S3 key (`make env` generates it) |
| `PRESIGNED_URL_SECONDS` | `300` | ≤ 300 (SECURITY.md §4) |
| `UPLOAD_MAX_MB_SPREADSHEET`, `UPLOAD_MAX_MB_PDF` | `50`, `25` | FR-FI-03 |
| `SMTP_STARTTLS`, `SMTP_USER`, `SMTP_PASSWORD` | | Required STARTTLS in staging/production |
| `CLAMD_HOST`, `CLAMD_PORT` | `clamav`, `3310` | |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | | Mailpit locally |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL_PRIMARY`, `LLM_MODEL_SECONDARY`, `AI_DAILY_COST_CAP_USD` | | **Unset by default; AI disabled** until OD-2 approval |
| `ENCRYPTION_KEYS` | `k1:<base64 32 bytes>` | MFA secrets (AES-256-GCM). Comma-separated `kid:key`; the **first is active** for new encryptions, all listed keys decrypt. **Rotation:** add the new key first in the list, deploy, run the re-encrypt command (added with the first rotation need; until then old keys stay listed), remove the old key. Never reuse a kid |
| `TRUSTED_PROXY_IPS` | `` (empty) | `X-Forwarded-For` honoured only from these peers (Caddy) |
| `SESSION_IDLE_MINUTES` / `SESSION_ABSOLUTE_HOURS` | `30` / `12` | |
| `SENTRY_DSN` | | Optional |
| `PUBLIC_BASE_URL` | `https://app.example.com` | Links in emails, cookie/Origin checks |
| `LOG_LEVEL` | `INFO` | |

The frontend receives only public build-time config (`VITE_API_BASE=/api/v1`); never secrets.

### 2.5 Docker images

- `backend/Dockerfile`: multi-stage (builder with `uv` → slim runtime), non-root user, read-only root filesystem where possible, no build tools in the final image, healthcheck on `/api/v1/health/live`, pinned base image by digest. Targets: `dev` (all deps, source bind-mounted) and `runtime`. One image runs as `api` (`gunicorn -k uvicorn.workers.UvicornWorker`) or `worker` (`python -m rebate.workers`) by command.
- OCR tooling (Tesseract/OCRmyPDF) is included in the **worker** image only.
- `frontend/Dockerfile`: build stage (`npm ci && npm run build`) → static files copied into the Caddy image (or served from a volume).

## 3. Production topology (initial)

```mermaid
flowchart LR
  Internet --> CD[Caddy :443]
  subgraph Host A - Docker network
    CD --> API[api x N]
    CD --> FE[static SPA]
    API --> PGX[(Postgres - per OD-3)]
    WK[worker x M] --> PGX
    WK --> CL[clamav]
  end
  API --> S3[(S3-compatible object storage)]
  WK --> S3
  PGX -->|WAL + dumps, encrypted| BK[(Backup storage, separate account)]
  S3 -.versioned, replicated.-> BK
  WK --> LLM[LLM provider - only after OD-2 approval]
  API --> OBS[Sentry / log store / uptime check]
```

- One production host (VPS or cloud VM, e.g. 4 vCPU / 8 GB to start) running Compose: `caddy`, `api`, `worker`, `clamav`, and (if self-run) `postgres`. A separate staging host.
- **Postgres (OD-3):** (a) self-run in Compose on a dedicated volume/host with `pgBackRest` or WAL-G; or (b) managed standard Postgres from a plain cloud provider. App connects over TLS (`sslmode=verify-full` when remote). Either way: backups, PITR, tested restores.
- Network: only Caddy exposes 80/443; Postgres/ClamAV/object storage are not exposed publicly; host firewall default-deny; SSH key-only, no root login, fail2ban; automatic security updates.
- Scale-up path: bigger instance → move worker to its own host → managed/dedicated Postgres with a read replica for reporting → multiple API replicas behind Caddy/load balancer. Kubernetes only with an ADR and a measured need.

### 3.1 Reverse proxy and SSL (Caddy)

`infra/caddy/Caddyfile` (sketch; final values set in S0/S11):

```
app.example.com {
    encode zstd gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        Content-Security-Policy "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        -Server
    }
    request_body { max_size 60MB }
    handle /api/* { reverse_proxy api:8000 }
    handle        { root * /srv/frontend; try_files {path} /index.html; file_server }
}
```
Caddy obtains and renews TLS certificates automatically (ACME); TLS 1.2+ only. Coarse rate limiting at the edge via a Caddy rate-limit plugin or the host firewall; fine-grained limits in the app.

## 4. CI/CD

GitHub Actions (or equivalent). Pipeline (see `TESTING_STRATEGY.md` §12):

1. **PR:** `ruff`, `mypy`, `import-linter`, unit/integration/api/db/isolation/golden tests against a Postgres service, `verify_business_logic_examples.py`, frontend lint/type/unit, OpenAPI client regeneration diff, `bandit`, `pip-audit`, `npm audit`, `gitleaks`, migration checks (empty→head, drift, lint rules).
2. **Main:** build and tag images (git SHA), push to a private registry, run Playwright e2e, ZAP baseline against an ephemeral stack.
3. **Deploy to staging** automatically; **production by manual approval**.
4. **Deploy procedure** (`infra/scripts/deploy.sh`): pull images → run migrations (`alembic upgrade head` with owner credentials, in a one-off container) → rolling restart `api` then `worker` → smoke tests (`/health/ready`, login, a read call) → record the release. Rollback = redeploy previous image tag; schema is forward-compatible for one release (expand/contract), otherwise restore from backup.
5. Branch protection: required checks, review (the owner approves stage completion, see `CODE_REVIEW_CHECKLIST.md`).

## 5. Logging

Structured JSON to stdout (collected by Docker); fields: `timestamp`, `level`, `service`, `request_id`, `tenant_id`, `user_id`, `route`, `status`, `latency_ms`, `job_id`. **Never** log bodies, tokens, passwords, file contents, or financial detail tied to named parties. Ship to a log store (e.g. Loki/Grafana Cloud/Papertrail; decision at S11) with 30–90 day retention. Audit trail is in the database (`audit_log`), not in log files.

## 6. Monitoring and alerting

| Signal | Tool | Alert |
|---|---|---|
| Uptime / TLS expiry | external checker (e.g. UptimeRobot/Better Stack) on `/health/ready` | Down > 2 min |
| Errors | Sentry (PII scrubbed) | New issue / spike |
| Jobs | Admin view + metric from Postgres | Any `failed`/dead job; queue age > 15 min |
| Postgres | `pg_stat_*`, disk, connections, replication/WAL archiving lag | Disk > 80%, archive failing, long-running transactions |
| Host | CPU/RAM/disk | > 85% sustained |
| Backups | Job result + checksum verification | Missed or failed |
| ClamAV | Signature freshness | Stale > 48 h |
| AI | Cost/day, failure rate | Cap reached, error spike |
| Security | Failed-login spikes, permission-denied spikes | Threshold |

## 7. Backups and recovery

- **Postgres:** nightly base backup + continuous WAL archiving (PITR). Self-run: `pgBackRest`/WAL-G to object storage in a **separate account/provider**; managed: provider PITR plus a nightly `pg_dump` copied elsewhere. Retention: 35 daily, 12 monthly. Backups encrypted.
- **Objects:** bucket versioning; nightly sync of changed objects to a second bucket/provider.
- **Config:** `infra/` and Compose files are in git; secrets in the secret store with documented recovery (owner holds an offline copy of critical keys, including `ENCRYPTION_KEY`).
- **Targets (initial, confirm with owner):** RPO ≤ 15 min, RTO ≤ 4 h.

### 7.1 Recovery runbooks (`infra/runbooks/`)

| Scenario | Steps (summary) |
|---|---|
| Restore DB to a point in time | Provision clean Postgres → restore base backup → replay WAL to target → verify with row counts and `alembic current` → run isolation/golden smoke tests → switch `DATABASE_URL` |
| Lost host | Provision new host → install Docker → pull images by tag → restore secrets → attach/restore DB → point DNS → smoke test |
| Corrupt object store | Restore versions or from the secondary bucket; verify `file_objects.sha256` against restored objects |
| Failed deployment | Roll back image tag; if a migration ran, assess forward-fix vs restore |
| Credential leak | Rotate affected secrets, revoke sessions (`UPDATE sessions SET revoked_at = now()`), review audit log, notify per policy |
| Single-tenant data incident | Use per-tenant export (Later) or point-in-time restore into a scratch DB and extract |
| Dead jobs | Admin UI: inspect error → retry or fix data; never edit queue tables by hand |

**Restore drill:** before the first pilot and monthly thereafter, restore the latest backup to a scratch environment, run the smoke/golden/isolation tests, and record duration and issues in `PROJECT_STATUS.md`.

## 8. Cost estimates (ESTIMATES, not verified prices; check current provider pricing)

| Item | Rough monthly (USD) |
|---|---|
| Production VPS 4 vCPU / 8 GB | 15–50 |
| Staging VPS | 8–20 |
| Managed Postgres (if OD-3 = managed) | 15–60 |
| Object storage + backups | 5–20 |
| Email, domain, uptime check | 10–40 |
| Sentry / logs | 0–30 |
| LLM and OCR | Variable: depends on document volume; likely cents to a few dollars per document during pilots (unverified) |
| **Total excluding LLM** | **≈ 60–220** |

## 9. Operational checklists

- **Before first pilot:** all S0–S11 stages approved; restore drill done; pen test scheduled/complete; AI provider terms approved (OD-2) or AI disabled; monitoring alerts tested; runbooks reviewed; data-processing terms and customer-facing security overview ready; support access procedure defined.
- **Release:** changelog, migration review, backup taken immediately before deploy, smoke tests, rollback plan noted.
- **Weekly:** check failed jobs, backup status, dependency alerts. **Monthly:** restore drill, secret/access review, cost review.
