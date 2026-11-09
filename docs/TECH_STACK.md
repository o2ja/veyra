# Tech Stack

> All technical choices below were delegated to the CTO role by the owner and are recorded as **Accepted** in `ARCHITECTURE_DECISIONS.md` unless marked PROPOSED. Versions are "current stable at scaffold time, pinned in lockfiles"; do not hard-code version numbers from this document.
> **Hard constraint (owner):** no Supabase, Firebase, or any BaaS as the application foundation. We own the backend and database.

## 1. Summary

| Layer | Choice | Purpose |
|---|---|---|
| Architecture | Modular monolith, two runtime processes (API, worker) from one codebase | Strong consistency, one deployable, enforceable boundaries |
| Backend language/framework | Python 3.12+, FastAPI | Decimal math, document parsing, matching, LLM SDKs, typed API with OpenAPI |
| ORM / migrations | SQLAlchemy 2.x (sync) + psycopg 3, Alembic | Explicit SQL control, Postgres features |
| Validation | Pydantic v2 | Request/response models, rules schema, AI output schema |
| Database | PostgreSQL 16+ | System of record, constraints, RLS, job queue |
| Job queue | Procrastinate (Postgres-backed) | Transactional enqueue, no extra infrastructure |
| Object storage | S3-compatible via boto3 (SeaweedFS locally and in CI; ADR-017) | Raw files and exports |
| Frontend | React 18 + TypeScript + Vite SPA | Back-office app; no SEO need |
| Reverse proxy / TLS | Caddy | Automatic HTTPS, simple config |
| Containers | Docker, Docker Compose | Reproducible local and production |
| Malware scanning | ClamAV (clamd sidecar) | Upload scanning |
| AI | Provider-abstracted LLM gateway (OCR fallback via Tesseract/OCRmyPDF) | Document interpretation only |

## 2. Backend

| Technology | Purpose | Notes / constraints |
|---|---|---|
| Python 3.14 (single version everywhere; ADR-027) | Runtime | `Decimal` is the money type |
| FastAPI | HTTP API | **Sync route handlers** (threadpool). OpenAPI generated and consumed by the frontend client |
| Uvicorn (+ Gunicorn workers in prod) | ASGI server | |
| SQLAlchemy 2.x, `psycopg[binary]` 3 | ORM/Core | Sync engine. Use Core/SQL for bulk loads (`COPY`, `INSERT … ON CONFLICT`) |
| Alembic | Migrations | Forward-only in shared environments |
| Pydantic v2, pydantic-settings | Schemas and config | Strict mode for money fields |
| Procrastinate | Job queue and periodic tasks | Uses Postgres `LISTEN/NOTIFY` and `FOR UPDATE SKIP LOCKED`. Spike in S2; fallback is a minimal in-house `SKIP LOCKED` queue (ADR-005) |
| `argon2-cffi` | Password hashing | Argon2id |
| `pyotp` | TOTP MFA | Plus recovery codes (hashed) |
| `cryptography` | AES-256-GCM for MFA secrets | S1 D-2; keys from env `ENCRYPTION_KEYS` |
| `segno` | TOTP QR code as SVG | S1 D-8; pure Python, BSD; the QR/URI is never logged |
| Common-password list (data) | Breached-password check | S1 D-3; Xato set (public domain), via SecLists (MIT); ≥ 12-char entries only; `modules/identity/data/` with `NOTICE` |
| `structlog` | Structured logging | JSON, request id + tenant id binding |
| `sentry-sdk` | Error tracking | Scrub PII; self-hostable alternative acceptable |
| `boto3` | S3-compatible storage client | Presigned URLs, short expiry |
| `clamd` (client) | Malware scanning | |
| `python-magic` | Magic-byte file type detection | |
| `charset-normalizer` | CSV encoding detection | |
| `openpyxl`, `python-calamine` | Excel read (strings only) | Read-only mode, no macros, no external links, no formulas evaluated |
| `XlsxWriter` | Excel export | Sanitise formula-injection (`SECURITY.md`) |
| `pdfplumber` (MIT) + `pypdfium2` | PDF text/table extraction and page rendering | **Not PyMuPDF**: AGPL licence risk for a closed-source SaaS (ADR-020) |
| `ocrmypdf` + Tesseract | OCR fallback for scans | Run in a resource-limited container |
| `rapidfuzz` | String similarity | Deterministic fuzzy scoring |
| `scipy` (`linear_sum_assignment`) | Optimal assignment for matching | Hungarian algorithm |
| `uuid-utils` | UUIDv7 generation | Time-ordered primary keys (PG16 has no native `uuidv7`) |
| `tenacity` | Retries with backoff | For external calls only (LLM, storage); not for DB transactions |
| Anthropic SDK (initial adapter) | LLM calls behind the AI gateway | Provider interface; **PROPOSED**: default provider and data policy need owner approval (OD-2) |

### Python tooling

`uv` (dependency management and lockfile), `ruff` (lint + format), `mypy` (`--strict` on `rebate_engine`, `rebate_matching`; standard elsewhere), `pytest`, `pytest-cov`, `hypothesis`, `import-linter` (module boundaries), `bandit` and `pip-audit` (security), `pre-commit`.

## 3. Database

**PostgreSQL 16+.** Why: `NUMERIC`, transactional DDL, range types with **exclusion constraints** (overlapping agreement validity), row-level security, JSONB for traces and extracted payloads, `pg_trgm` for fuzzy candidate retrieval, partitioning, advisory locks, mature backup/PITR.

Extensions: `btree_gist`, `pg_trgm`, `citext`.

Roles: `rebate_owner` (runs migrations; owns objects), `rebate_app` (API/worker runtime; no `BYPASSRLS`, not owner), `rebate_readonly` (reporting/debug, tenant-scoped via RLS). Details in `DATABASE_SCHEMA.md` §2 and `SECURITY.md`.

Local dev: a **new** Postgres 16 container (or a native local install) created from scratch via Docker Compose and migrations. No data is imported from anywhere.

Not approved: MySQL, MongoDB or any document store for financial data, SQLite (except unit tests of pure code that never touch persistence), any BaaS-hosted Postgres with proprietary auth/storage coupling.

## 4. Frontend

| Technology | Purpose |
|---|---|
| React 18 + TypeScript (strict) | UI |
| Vite | Build/dev server |
| React Router 7 (library/data mode only; no framework mode or SSR) | Routing |
| TanStack Query | Server state, caching, polling jobs |
| TanStack Table (+ `@tanstack/react-virtual`) | Data grids with large row counts |
| React Hook Form + Zod | Forms and client-side validation (never a substitute for server validation) |
| Tailwind CSS + Radix UI primitives (shadcn/ui pattern, copied into repo) | Accessible components, no runtime UI library lock-in |
| `openapi-typescript` + `openapi-fetch` | Typed API client generated from backend OpenAPI; build fails on drift |
| `decimal.js` | Display-side decimal handling. The frontend **never computes financial results**; it displays server-computed strings |
| Vitest + Testing Library | Unit/component tests |
| Playwright | End-to-end tests |
| ESLint + Prettier | Lint/format |

Rules: SPA only, no SSR, no Next.js API routes, no second backend. Money crosses the API as **decimal strings** (`"2200.00"`), never JSON numbers.

Not approved: Next.js (not needed; would invite a second backend), Redux (TanStack Query covers server state), heavy component suites that conflict with Tailwind, any client-side financial calculation beyond display formatting.

## 5. File processing and AI

| Concern | Tool | Where it runs |
|---|---|---|
| Virus scan | ClamAV `clamd` | Sidecar container; worker `scan_file` job |
| File type check | `python-magic` + extension + MIME | API at upload; worker re-check |
| CSV/XLSX parse | stdlib `csv`, `openpyxl`, `python-calamine` | Worker, resource-limited |
| PDF text/tables | `pdfplumber` | Worker |
| OCR fallback | `ocrmypdf`/Tesseract | Worker with CPU/memory/time limits |
| Structured extraction | LLM via **AI gateway** with JSON-schema output | Worker only |
| Fuzzy matching | `rapidfuzz` + `scipy` | Pure package `rebate_matching` |
| Embeddings | **Not used in Release 1** | Revisit for SKU alias mapping |

See `AI_PROCESSING.md`.

## 6. Infrastructure

| Concern | Choice |
|---|---|
| Local | Docker Compose: `postgres`, `mailpit`, `api`, `frontend` (dev server); from S2: `seaweedfs`, `clamav` (profile `s2`), `worker` |
| Production (initial) | Single-host Docker Compose (VPS) behind Caddy; separate staging host; S3-compatible object storage; Postgres hosting per OD-3 |
| CI/CD | GitHub Actions (or equivalent): lint, type-check, tests (real Postgres service), isolation suite, image build, deploy by SSH + `docker compose pull` |
| Monitoring | Sentry (errors), uptime check, structured logs shipped to a log store, Postgres metrics, failed-job alerts |
| Secrets | Environment variables injected at runtime from a secrets store/`.env` outside the repo; never committed |
| Email | SMTP (Mailpit locally; transactional provider in production) |

Details: `DEPLOYMENT.md`.

## 7. Explicitly not approved

| Alternative | Reason |
|---|---|
| Supabase, Firebase, Appwrite, PocketBase, any BaaS | Owner constraint: full ownership of backend and DB |
| Microservices, Kubernetes, service mesh, event-sourcing frameworks | Unnecessary complexity (ADR-001) |
| Kafka, RabbitMQ, Redis (initially) | Postgres covers queue and caching needs at this scale; add only on measured need with an ADR |
| GraphQL | REST + OpenAPI is simpler and sufficient |
| JWT in `localStorage` | XSS-exposed; use server-side sessions in HttpOnly cookies |
| NoSQL for financial data | Integrity and constraints required |
| PyMuPDF | AGPL; licensing risk |
| LLM-computed numbers, LLM tool-use that writes to the DB | Violates core safety rule |
| A general scripting language/DSL for rules | Un-auditable; see ADR-009 |
| Floats for money anywhere | See NFR-01 |
| ORM auto-migration (`create_all`) | All schema changes go through Alembic |

## 8. Adding a dependency

Allowed without approval: a small, well-maintained, permissively licensed library that does not alter the architecture (a date helper, a test utility). Must be justified in the PR/commit message and in this file.
Requires owner approval + ADR: new datastore, queue, cache, framework, anything networked, anything with AGPL/GPL/SSPL licence, anything that changes how data flows or is secured.
