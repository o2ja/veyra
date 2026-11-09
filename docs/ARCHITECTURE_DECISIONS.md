# Architecture Decision Records

> Update this file whenever an important decision is made or changed. Never edit an *Accepted* ADR in place to reverse it: add a new ADR that supersedes it and mark the old one `Superseded by ADR-xxx`.
> Status values: **Accepted** (owner delegated technical decisions to the CTO role, 2026-11-09), **PROPOSED** (needs owner approval before reliance), **Superseded**.

| ID | Title | Status |
|---|---|---|
| ADR-001 | Modular monolith, two processes | Accepted |
| ADR-002 | Python 3.12 + FastAPI backend | Accepted |
| ADR-003 | PostgreSQL as the only datastore (+ object storage) | Accepted |
| ADR-004 | SQLAlchemy 2.0 sync + psycopg 3 | Accepted |
| ADR-005 | Postgres-backed job queue (Procrastinate) | Accepted (spike in S2) |
| ADR-006 | Multi-tenancy: shared schema, composite FKs, RLS | Accepted |
| ADR-007 | Server-side sessions, TOTP MFA | Accepted |
| ADR-008 | Pure calculation and matching packages | Accepted |
| ADR-009 | Rules as validated JSON; no scripting DSL | Accepted |
| ADR-010 | Immutable agreement versions; split-supersede | Accepted |
| ADR-011 | One agreement draft pipeline for manual and AI | Accepted (owner requirement) |
| ADR-012 | AI gateway with write restrictions; provider/data policy | Accepted (design) · **PROPOSED** (default provider and data terms, OD-2) |
| ADR-013 | Claim batches freeze on submission; adjustments | Accepted |
| ADR-014 | Deterministic-first reconciliation | Accepted |
| ADR-015 | Money representation | Accepted |
| ADR-016 | React SPA with generated API client | Accepted |
| ADR-017 | S3-compatible object storage | Accepted |
| ADR-018 | Docker Compose + Caddy deployment | Accepted |
| ADR-019 | First vertical: electrical distribution | **PROPOSED** (OD-1) |
| ADR-020 | PDF libraries and licensing | Accepted |
| ADR-021 | Staged delivery with approval gates | Accepted |
| ADR-022 | Synthetic data policy | Accepted |
| ADR-023 | Production hosting of Postgres and app | **PROPOSED** (OD-3, OD-4) |
| ADR-024 | Single currency (USD) in Release 1 | Accepted (assumption A-08) |
| ADR-025 | Time and date handling | Accepted |
| ADR-026 | Audit log design and sealing | Accepted |

---

## ADR-001 Modular monolith, two processes
- **Context:** Financial product with strong consistency needs, a one-person review/approval model, and an implementer (Claude Code) working in small stages.
- **Problem:** Choose a deployment/architecture shape.
- **Decision:** One codebase with enforced module boundaries (`import-linter`), deployed as `api` and `worker` processes against one Postgres.
- **Alternatives:** Microservices (network failures, distributed transactions, ops overhead); single process with in-request long work (blocks requests, poor retry).
- **Consequences:** Simple deploys and transactions. Discipline required to keep boundaries; CI enforces it. A module (likely document processing) can be split out later because seams already exist.

## ADR-002 Python 3.12 + FastAPI
- **Context:** Domain is decimal math, spreadsheet/PDF parsing, fuzzy matching, LLM integration.
- **Problem:** Backend language/framework.
- **Decision:** Python with FastAPI, Pydantic v2.
- **Alternatives:** Node/TypeScript (NestJS): good API ergonomics, weaker document/numeric ecosystem, clumsier decimal handling. Django: more batteries (admin), heavier for an API-only product and ties to its ORM.
- **Consequences:** Best library fit. Python is slower than Go/Rust but the workload is I/O and batch bound; hot loops (engine) are vectorisable or chunked. Needs strict typing discipline (`mypy`).

## ADR-003 PostgreSQL only (+ object storage)
- **Context:** Needs exact decimals, constraints, transactions, range exclusion, RLS, JSONB, queue.
- **Decision:** PostgreSQL 16+ is the only datastore; no Redis/Kafka initially. Files in object storage.
- **Alternatives:** MySQL (weaker constraints, no range exclusion/RLS), document stores (no integrity).
- **Consequences:** One system to back up and reason about. Caching/queue needs are met by Postgres until measured otherwise (new ADR required to add Redis).

## ADR-004 SQLAlchemy 2.0 sync + psycopg 3
- **Context:** Tenant context is set per transaction (`SET LOCAL`); batch work dominates; simplicity matters.
- **Decision:** Sync SQLAlchemy with psycopg 3; FastAPI sync handlers (threadpool). Bulk loads via Core/`COPY`.
- **Alternatives:** Async SQLAlchemy + asyncpg: more concurrency per process, but more footguns (lazy loading, session scoping, mixing with CPU-bound parsing).
- **Consequences:** Simpler code and testing. Concurrency scales with threadpool/process count; revisit only if measured as a bottleneck.

## ADR-005 Postgres-backed job queue (Procrastinate)
- **Context:** Jobs must enqueue transactionally with data changes; avoid more infrastructure.
- **Decision:** Procrastinate (Postgres `SKIP LOCKED` + `LISTEN/NOTIFY`), jobs and periodic tasks.
- **Alternatives:** Celery+Redis/RabbitMQ (extra infra, dual-write problem); homegrown queue.
- **Consequences:** Transactional enqueue. S2 includes a spike confirming sync task handling, retry controls, and per-job tenant context; **fallback** is a minimal homegrown `SKIP LOCKED` queue behind the same `enqueue()` interface (`core/jobs.py`) so swapping does not touch modules.

## ADR-006 Multi-tenancy: shared schema, composite FKs, RLS
- **Context:** B2B financial data; cross-tenant leakage is the worst security failure.
- **Decision:** `tenant_id` on every tenant-owned table; `UNIQUE (tenant_id, id)` and composite FKs; RLS with `FORCE ROW LEVEL SECURITY`; `SET LOCAL app.tenant_id` per transaction; app role without `BYPASSRLS` and not table owner; isolation test suite in CI.
- **Alternatives:** Schema-per-tenant (migration fan-out, many objects); DB-per-tenant (operational cost; keep as a future option for very large customers); application-only filtering (single bug = breach).
- **Consequences:** Defence in depth. Slight query overhead; pool/transaction discipline required. Backups contain all tenants; per-tenant restore tooling is a later concern.

## ADR-007 Server-side sessions, TOTP MFA
- **Decision:** Opaque session token in `HttpOnly; Secure; SameSite=Lax` cookie, sessions in Postgres, CSRF header on writes, Argon2id, TOTP MFA default-on, recovery codes.
- **Alternatives:** JWT in localStorage (XSS exposure, hard revocation); JWT cookies (revocation complexity); third-party identity provider (violates ownership preference, but SSO via OIDC/SAML is an R2 add-on).
- **Consequences:** Instant revocation, simple threat model. DB hit per request (acceptable; indexed).

## ADR-008 Pure calculation and matching packages
- **Decision:** `rebate_engine` and `rebate_matching` are importable without the app, DB, network, clock, or randomness. Input/output are frozen dataclasses/Pydantic models. Engine returns result + trace.
- **Alternatives:** Calculation inside SQL (hard to test/trace), inside ORM models (coupled to persistence).
- **Consequences:** Property-based and golden testing is straightforward; runs can be re-derived from stored inputs; engine version recorded with every result.

## ADR-009 Rules as validated JSON; no scripting DSL
- **Decision:** Agreement terms are stored as a versioned JSON schema (`rules_schema_version`), validated by Pydantic. Calculation *types* are a closed set (`BUSINESS_LOGIC.md` §3). Truly unusual manufacturer rules go in a named, code-reviewed, unit-tested adapter (`rebate_engine/adapters/`), selected by `rules.adapter` and recorded in the trace.
- **Alternatives:** Embedded expression language / Turing-complete DSL (un-auditable, un-testable, injection surface); hard-coding per manufacturer (does not scale).
- **Consequences:** Every rule is inspectable and testable. New calculation types require a code change plus tests, which is intentional.

## ADR-010 Immutable agreement versions; split-supersede
- **Decision:** Active versions never change. Amendments create new versions; mid-period amendments use split-supersede (`BUSINESS_LOGIC.md` §9). Exclusion constraint prevents overlapping active versions per agreement. Calculation runs pin version ids.
- **Alternatives:** Mutable agreements with history table (risk of silent change); full bitemporal tables (more complexity than needed).
- **Consequences:** Past results stay reproducible. Slightly more records per amendment.

## ADR-011 One agreement draft pipeline for manual and AI
- **Context:** Owner requirement: manual and AI-assisted creation are both essential and must share structure, validation, and approval.
- **Decision:** All creation paths produce `agreement_drafts` with identical schema (`source` = `manual` | `spreadsheet_template` | `ai_extraction`), validated by one `AgreementValidator`, approved through one service, producing one `agreement_versions` shape.
- **Alternatives:** Separate AI and manual tables/flows (drift, duplicated validation).
- **Consequences:** The AI path can be improved or turned off without affecting calculations. Manual entries also go through approval; four-eyes is a workspace setting.

## ADR-012 AI gateway with write restrictions; provider and data policy
- **Decision (design, Accepted):** All LLM/OCR calls go through `extraction/gateway.py`: schema-constrained output, prompt versioning, input/output logging with redaction, cost tracking, per-tenant opt-out, retries. It runs under a DB role that can write only `document_extractions`, `ai_invocations`, `agreement_drafts` (and remittance extraction staging). AI never sees or produces claim amounts used in financial records.
- **Decision (PROPOSED, OD-2):** Which provider is the default and under what data terms. Initial adapter implemented against the Anthropic API behind a provider interface; the choice is validated on the golden document set in S6 and must use a provider/contract with no-training and zero/limited retention terms. **Owner must approve before any real customer document is sent.** Synthetic documents may be used meanwhile.
- **Alternatives:** Self-hosted open models (privacy upside; lower accuracy, high ops cost for now).
- **Consequences:** Swap-able provider; a golden set makes regressions visible.

## ADR-013 Claim batches freeze on submission; adjustments
- **Decision:** Submission freezes `claim_lines` (DB trigger blocks UPDATE/DELETE on frozen rows). Corrections create `claim_adjustments`. A calculation result can belong to at most one non-void claim line (partial unique index).
- **Consequences:** Reproducible audit evidence; changes require explicit, approved adjustments.

## ADR-014 Deterministic-first reconciliation
- **Decision:** Tiered deterministic matching (exact reference → composite key → fuzzy assignment → bounded subset-sum). Only exact tiers auto-confirm. No LLM in match decisions. LLM may draft an explanation from the deterministic trace.
- **Consequences:** Explainable and testable; some manual confirmation work remains by design.

## ADR-015 Money representation
- **Decision:** `NUMERIC(18,4)` amounts, `NUMERIC(18,6)` unit prices/costs, `NUMERIC(18,6)` quantities, `NUMERIC(9,6)` rates (fractions, e.g. `0.025`). `Decimal` in Python with explicit context (precision 28) and `ROUND_HALF_UP` unless the agreement specifies otherwise. JSON as strings.
- **Alternatives:** Integer cents (breaks for sub-cent unit prices common in distribution), floats (forbidden).
- **Consequences:** Exactness; careful rounding placement documented per agreement.

## ADR-016 React SPA with generated API client
- **Decision:** Vite + React + TypeScript SPA, TanStack Query/Table, Tailwind + Radix, client generated from OpenAPI.
- **Alternatives:** Next.js (adds server runtime/API-route temptation), server-rendered templates (poor fit for heavy grids).
- **Consequences:** Clear front/back separation; API drift breaks the build.

## ADR-017 S3-compatible object storage
- **Decision:** Use the S3 API; MinIO locally; any S3-compatible provider in production.
- **Consequences:** Portable; presigned URLs; lifecycle and versioning policies.

## ADR-018 Docker Compose + Caddy
- **Decision:** Compose for local and initial production; Caddy for TLS and reverse proxy. Kubernetes deferred until justified.
- **Consequences:** Low operational burden; scale-up path: bigger host, separate worker host, read replica.

## ADR-019 First vertical: electrical distribution — PROPOSED
- **Context:** Owner is unfamiliar with HVAC/electrical and asked for a comparison and recommendation. *The comparison below is based on general domain knowledge of these industries, not verified research. Validate in customer conversations (A-01…A-12).*

| Dimension | Electrical distribution | HVAC/R distribution |
|---|---|---|
| Typical manufacturers | Eaton, Schneider Electric, Siemens, Hubbell, Legrand, ABB, Southwire, Cooper/Signify lighting | Carrier, Trane, Lennox, Rheem/Ruud, Daikin, Goodman |
| Products | Very large catalogue (100k+ SKUs): breakers, switchgear, wire, lighting, controls | Equipment (high-value lines), parts, sheet metal, refrigerants |
| Where claims arise | **Project/quote-based ship-and-debit (SPA) is pervasive**: many line items, small dollar differences per line; rebates via buying groups (e.g. NAED community) and manufacturer growth/volume programs | Equipment program rebates, promotional/seasonal incentives, volume/growth rebates, some deviated pricing; fewer lines, higher dollars per line |
| UoM complexity | High (wire by foot/reel, boxes, each) | Lower |
| Price volatility | Copper/commodity-linked price changes | Seasonal programs, regulatory (refrigerant) changes |
| Claim line volume | High → best showcases line-level reconciliation and short-pay detection | Medium |
| Complexity for us | Higher: UoM, quantity caps, many SKUs | Lower per agreement, but programs/tiers more narrative |
| Seasonality | Low | High (summer) |
| Buying groups | NAED-related groups, IMARK, Affiliated Distributors | HARDI, AD, others |

- **Decision (PROPOSED):** Start with **electrical**. The product's core wedge (line-level SPA claims and short-pay recovery) is most pronounced there, the problem repeats across many manufacturers with similar structures, and the UoM/cap complexity forces the engine to be general. HVAC is the second vertical and mainly exercises program rebates and tiers.
- **Alternatives:** HVAC first: simpler engine, quicker first demo, but fewer line-level claims to recover and more narrative contracts.
- **Consequences:** Synthetic data and golden tests lead with electrical scenarios but include HVAC-style program rebates so both are covered. **Switching cost is low** because no code is vertical-specific. **Owner approval required (OD-1).**

## ADR-020 PDF libraries and licensing
- **Decision:** `pdfplumber` (MIT) and `pypdfium2` (permissive) for text/tables/rendering; OCRmyPDF/Tesseract for scans. PyMuPDF excluded (AGPL, or commercial licence).
- **Consequences:** No copyleft obligations for a closed-source SaaS. Slightly less capable rendering; acceptable.

## ADR-021 Staged delivery with approval gates
- **Decision:** Work proceeds stage by stage (`DEVELOPMENT_ROADMAP.md`). Before each stage: present plan and expected outcomes for owner approval. After each stage: run tests, complete `CODE_REVIEW_CHECKLIST.md`, update `PROJECT_STATUS.md`, report to owner, obtain approval to proceed.
- **Consequences:** Slower than one big build; far lower integration and rework risk.

## ADR-022 Synthetic data policy
- **Decision:** All test data is synthetic, generated deterministically from seeds by `testdata/generate.py`, labelled (`SYNTHETIC` in file content, names, and metadata; fictional manufacturer/customer names), and accompanied by ground-truth files with expected results. Real customer data is never committed to the repository.
- **Consequences:** Reproducible tests; synthetic data is not proof of real-world fidelity (A-12).

## ADR-023 Production hosting — PROPOSED
- **Context:** Owner wants control and portability; local dev is self-run Postgres.
- **Options:** (a) Self-run Postgres on a VPS/volume with pgBackRest/WAL-G; (b) managed *standard* Postgres from a plain cloud provider (not a BaaS).
- **Recommendation:** (b) for the first paying customers (managed PITR, patching), because the app depends only on standard Postgres and remains portable; (a) is fully supported by the same artefacts. Decide before S11.
- **Region:** US region for US customers (OD-4).

## ADR-024 Single currency (USD)
- **Decision:** Release 1 supports USD only; `currency_code` column is present on money rows and validated equal to the tenant currency.
- **Consequences:** No FX logic; future multi-currency is additive.

## ADR-025 Time and date handling
- **Decision:** `timestamptz` in UTC for instants; `date` for business dates (invoice date, valid period). Tenant has an IANA business time zone used to compute "today" for deadlines. Calendar-day arithmetic; no weekend/holiday rolling (conservative).

## ADR-026 Audit log design and sealing
- **Decision:** Inline append-only inserts in the same transaction as the change; per-tenant monotonic `seq`; periodic `audit_seal` job chains entries (`entry_hash = H(prev_hash || canonical_entry)`), written to a separate `audit_seals` table. App role has INSERT/SELECT only; triggers reject UPDATE/DELETE.
- **Alternatives:** Inline hashing (serialises writes per tenant); external immutable store (later).
- **Consequences:** Low write contention; tamper-evidence with a small delay.
