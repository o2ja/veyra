# CLAUDE.md — Permanent instructions for Claude Code

This file is loaded in every session. It is short on purpose; details live in `docs/`. **The documentation is the source of truth.** If the docs and the code (or the owner's request) conflict, say so and ask. Never silently change either side.

## 1. Project context

A commercial multi-tenant SaaS: **Veyra Recover** (company: **Veyra AI**), rebate and claim recovery for wholesale distributors (first vertical: electrical, pending owner approval OD-1). It finds, claims and recovers money owed by manufacturers: SPA / ship-and-debit claims, volume rebates, remittance reconciliation and a recovery queue. Users: `admin`, `approver`, `analyst`, `viewer`.

Financial accuracy, explainability, tenant isolation and auditability matter more than speed or feature count. AI assists with reading documents; **it never decides eligibility or amounts**.

Roles in this project: the **owner** reviews, gives feedback and approves decisions and stage gates. **You** plan and implement. Technical choices that don't significantly affect product, architecture, security or business direction are yours to make (record them in an ADR if notable). Ask for approval on the rest.

## 2. The 15 strict rules

1. Always read the relevant documentation before implementing a feature.
2. Never introduce a new technology or dependency without justification and approval when it changes the approved architecture.
3. Never use Supabase, Firebase, or another BaaS as a replacement for our self-managed backend and database architecture.
4. Never use mock data as a substitute for required production functionality.
5. Never fake API responses, database persistence, authentication, or successful operations.
6. Never leave critical business logic as TODOs or placeholder implementations.
7. Never perform financial calculations using LLM-generated answers.
8. Every financial calculation must have corresponding automated tests.
9. Every database change must have a proper migration.
10. Always validate user input on the backend.
11. Always enforce tenant isolation and authorization on the server.
12. Never expose secrets or sensitive data in frontend code or logs.
13. Keep the architecture modular and avoid unnecessary complexity.
14. Update the relevant documentation when implementation changes an approved design.
15. Never claim a feature is complete without actually testing it.

If a requirement is ambiguous or an important architectural decision is missing, **ask first**.

## 3. Working process (stage gates)

Stages S0–S11 are defined in `docs/DEVELOPMENT_ROADMAP.md` and broken into checkbox tasks in `docs/IMPLEMENTATION_PLAN.md`.

1. **Kickoff report** before each stage: scope, tasks, files/modules, risks, open questions, test plan, expected outcome. **Wait for owner approval.**
2. Implement in small steps; commit-sized, tested increments. Do not build ahead of the approved stage.
3. **Verify**: run the full test suite and the isolation suite; exercise the feature manually.
4. Complete `docs/CODE_REVIEW_CHECKLIST.md`.
5. Update `docs/PROJECT_STATUS.md`, `IMPLEMENTATION_PLAN.md` checkboxes and any changed docs.
6. **Completion report**: what was built, what was tested and how, results, deviations from the docs, known issues, decisions needed. **Wait for owner approval** before the next stage.

Report honestly. A failing or skipped test, an unverified claim or a limitation is stated plainly. Say "not tested" when something was not tested.

## 4. Document map — read before working on…

| Working on | Read |
|---|---|
| Anything | `docs/PROJECT_OVERVIEW.md`, `docs/ARCHITECTURE.md`, this file |
| Requirements, acceptance criteria | `docs/PRODUCT_REQUIREMENTS.md` |
| Libraries, versions, what's rejected | `docs/TECH_STACK.md` |
| Why a choice was made / changing one | `docs/ARCHITECTURE_DECISIONS.md` |
| Tables, RLS, migrations, constraints | `docs/DATABASE_SCHEMA.md` |
| Endpoints, errors, pagination | `docs/API_SPECIFICATION.md` |
| Any money, eligibility, tiers, rounding, reconciliation, deadlines | `docs/BUSINESS_LOGIC.md` |
| Extraction, LLM use | `docs/AI_PROCESSING.md` |
| Auth, roles, uploads, secrets, audit | `docs/SECURITY.md` |
| Tests and test data | `docs/TESTING_STRATEGY.md`, `docs/TEST_DATA_SPEC.md` |
| Environments, CI/CD, backups | `docs/DEPLOYMENT.md` |
| What to build next | `docs/DEVELOPMENT_ROADMAP.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/PROJECT_STATUS.md` |
| Reviewing your own work | `docs/CODE_REVIEW_CHECKLIST.md` |

Status labels used in docs: **APPROVED**, **PROPOSED** (awaiting owner), **ASSUMPTION**, **SYNTHETIC**. Do not implement PROPOSED items that depend on an unresolved decision without asking.

## 5. Architecture summary

- **Modular monolith**: one codebase, two processes (API and worker), one PostgreSQL 16+, S3-compatible object storage. React SPA served as static files behind Caddy.
- Pure, framework-free packages: `rebate_engine` (calculation) and `rebate_matching` (reconciliation). No DB, network, clock or randomness inside them; enforced by `import-linter`.
- Modules own their tables and expose services; other modules call services, not repositories or foreign tables.
- Multi-tenancy: shared schema, `tenant_id` on every tenant table, composite unique + composite FKs, **RLS ENABLE + FORCE**, `SET LOCAL app.tenant_id` per transaction. The app connects as `rebate_app` (no BYPASSRLS, not owner). Cross-tenant access returns 404.
- Agreements: manual entry, spreadsheet template and AI extraction all produce an `agreement_draft` → one `AgreementValidator` → human approval → immutable `agreement_versions`.
- Calculations are deterministic, traced and reproducible. Claimed/submitted data is frozen; corrections are adjustments, never edits.
- Background work via the Postgres-backed job queue behind `core/jobs.py`.

## 6. Technology (approved; see `TECH_STACK.md`)

Backend: Python 3.14 (one version locally, in Docker and CI; ADR-027), FastAPI (sync handlers), SQLAlchemy 2.x + psycopg 3, Alembic, Pydantic v2, Procrastinate (pending S2 spike), structlog. Frontend: React 18, TypeScript, Vite, React Router, TanStack Query/Table, React Hook Form + Zod, Tailwind + Radix, generated OpenAPI client. Tooling: uv, ruff, mypy, pytest, hypothesis, Vitest, Playwright, import-linter, bandit, pip-audit, gitleaks.

Not approved (ask first): BaaS, microservices/Kubernetes, Redis/Kafka/RabbitMQ, GraphQL, Next.js, Redux, JWT in localStorage, NoSQL for financial data, PyMuPDF (AGPL), LLM-computed numbers, a rules scripting DSL, floats for money, `create_all`.

## 7. Folder structure

```
/
├─ CLAUDE.md  README.md
├─ docs/                      all design docs (source of truth)
├─ testdata/                  synthetic generator, verifier, generated files, ground truth
├─ backend/
│  ├─ pyproject.toml  uv.lock  alembic.ini  importlinter.ini  Dockerfile
│  ├─ migrations/             Alembic (owner role)
│  ├─ src/rebate/
│  │  ├─ core/                config, db, errors, ids, money, clock, logging (jobs from S2)
│  │  ├─ api/                 app factory, deps, middleware, router registry
│  │  ├─ modules/<name>/      models, schemas, repository, service, policies, router, jobs
│  │  └─ workers/             worker entrypoint
│  ├─ src/rebate_engine/      pure calculation package
│  ├─ src/rebate_matching/    pure matching package
│  └─ tests/                  unit, integration, api, db, isolation, engine_golden, extraction, security, performance
├─ frontend/                  src/ (app, features/<name>, components, api (generated), lib), e2e/
├─ infra/                     docker (compose.dev.yml), caddy, scripts, runbooks
├─ Makefile  .env.example  .pre-commit-config.yaml
└─ .github/workflows/
```
Modules (authoritative list: `docs/ARCHITECTURE.md` §3): `identity, tenancy, audit, files, masterdata, ingestion, agreements, extraction, claims, reconciliation, recovery, reporting`. Remittance tables belong to `reconciliation`; notifications to `recovery`. Test-folder layout: `ARCHITECTURE.md` §2.

## 8. Naming conventions

- Python: `snake_case` modules/functions, `PascalCase` classes, `UPPER_SNAKE` constants. Type hints everywhere; `mypy --strict` for engine and matching.
- TypeScript: `PascalCase` components, `camelCase` variables, files `kebab-case`. `strict` mode on.
- Database: plural `snake_case` tables, singular column names, `<table>_id` foreign keys, `tenant_id` first. Constraint/index names explicit (`uq_`, `ck_`, `fk_`, `ix_`). UUIDv7 primary keys. Money in `NUMERIC`; rates as fractions.
- API: `/api/v1/<plural-resource>`, kebab-case path segments, `snake_case` JSON, money as decimal strings, ISO-8601 dates, UTC timestamps.
- Migrations: one logical change each, descriptive names, reviewed SQL for RLS/triggers.
- Exception/status codes: lowercase snake_case, defined once in the docs and in code enums.

## 9. Backend rules

- Routers: parse/validate, check permission, call a service, return a schema. No business logic.
- Services own transactions and orchestration; repositories own queries. All queries are tenant-scoped by RLS **and** by explicit parameters where cheap.
- Every route declares a permission; a meta-test fails otherwise.
- Money is `Decimal` (prec 28, `ROUND_HALF_UP` as specified). Reject floats at boundaries. No arithmetic on money in SQL strings built from user input.
- Time comes from `core/clock.py`; never call the wall clock inside business logic. Business dates are `date`; timestamps are UTC.
- Long or bulk work runs in jobs: chunked, resumable, idempotent, tenant-context aware.
- Write audit events in the same transaction as the change.
- LLM access only through the AI gateway with the restricted DB role.

## 10. Frontend rules

- Use the generated API client; never hand-write duplicate API types.
- No business calculations in the UI; display values from the server. Use `decimal.js` only for display formatting.
- The server is the authority on permissions; the UI merely hides what the user cannot do.
- No secrets, tokens or sensitive data in code, `localStorage` or logs. Session cookie is HttpOnly.
- Handle loading, empty, error and permission-denied states. Accessible by default.

## 11. Database and migration rules

- Alembic only; never `create_all`, never manual DDL outside migrations.
- **Exception (tests only):** a test may run DDL solely inside a transaction that is always rolled back, in a scratch schema (never `public`), to prove that schema lints and migration helpers detect or enforce what they claim.
- Every tenant table: `tenant_id`, `UNIQUE (tenant_id, id)`, composite FKs, RLS enabled and forced, policy via the shared helper. The schema-lint tests enforce this.
- Invariants in constraints/triggers, not only in code. Frozen data has immutability triggers.
- Destructive migrations need a note in the kickoff report and owner approval.

## 12. API rules

- Follow `API_SPECIFICATION.md`: error envelope, cursor pagination, idempotency keys for creating or submitting operations, `If-Match` for concurrent edits.
- Backend validation is mandatory even if the UI validates. Errors never leak internals or other tenants' existence.
- Change the OpenAPI contract deliberately; regenerate the client; update the spec doc.

## 13. Security rules

Follow `docs/SECURITY.md`. Highlights: Argon2id, MFA, server-side sessions, CSRF protection, strict upload validation + malware scan, quarantine before use, presigned URLs short-lived, secrets only from environment/secret store, no PII/financial data in logs, uploaded document text is untrusted input (prompt injection), formula-injection protection in exports.

## 14. Testing rules

- Real PostgreSQL in integration tests; do not mock the database or RLS.
- Expected values for financial tests come from independent derivation (hand calculation, reference implementation, `testdata/ground_truth`), never from copying the output of the code under test.
- Golden, property (Hypothesis) and edge-case tests for the engine and matching.
- The tenant-isolation suite must run in CI and stay green; extend it with every new tenant table and route.
- A feature counts as done only when its tests pass **and** it was exercised manually. Do not delete or weaken a test to make it pass.
- Synthetic data is always labelled; never put real customer data in the repo.

## 15. Error handling

- Domain errors are typed (`DomainError` subclasses) with stable codes; the API maps them to the envelope.
- Fail loudly on invariant violations; never swallow exceptions or return fabricated defaults.
- Jobs retry with backoff only when safe; permanent failures go to a visible dead-letter state.
- Partial failure must leave consistent data (transaction or resumable state), never silently half-done.

## 16. Documentation requirements

- Update docs in the same change that alters behaviour (rule 14). Notable technical choices get an ADR (`ARCHITECTURE_DECISIONS.md`).
- Keep `PROJECT_STATUS.md` and the `IMPLEMENTATION_PLAN.md` checkboxes truthful.
- Cross-reference, don't duplicate: link to the owning document instead of copying rules.
- Do not edit a document's approved decision without flagging it to the owner.

## 17. Small, staged development

Prefer the smallest vertical slice that proves the design, test it, then extend. Avoid speculative features, premature optimisation and new abstractions without a second use. If something is bigger than a stage allows, stop and propose a split.
