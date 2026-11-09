# Project Status

> Living document. Updated at the end of every stage and whenever a decision, risk or known issue changes. Last updated: 2026-11-10.

## 1. Current position

| Item | State |
|---|---|
| Phase | **Release 1 build** |
| Current stage | **S1 Identity, tenancy, RBAC, audit: implementation complete, awaiting owner sign-off** (`docs/reports/S1_COMPLETION_REPORT.md`) |
| Application code | S0 skeleton + S1: identity, tenancy, audit modules; auth/MFA/sessions/CSRF; admin UI; isolation suite with negative control |
| Synthetic test data | Generated and self-checked (`testdata/`) |

## 2. Completed

- [x] Product, architecture and technical analysis (earlier phase)
- [x] Owner decisions recorded (local PostgreSQL, no BaaS, React SPA + separate backend, manual + AI agreements on one pipeline, staged delivery with approvals)
- [x] Documentation package (`docs/`, `CLAUDE.md`, `README.md`)
- [x] Synthetic dataset generator and ground truth (`testdata/generate.py`)
- [x] S0 Foundation and tooling (signed off 2026-11-09)
- [x] Independent check of every worked example in `BUSINESS_LOGIC.md` (`testdata/verify_business_logic_examples.py`, passes)

## 3. In progress

S1 implemented and verified; awaiting owner sign-off. Evidence and open points: `docs/reports/S1_COMPLETION_REPORT.md`. CI green on `main` (jobs: backend incl. isolation negative control, frontend, e2e (Playwright W1–W2 + axe), api-contract, secrets, images).

## 4. Pending

Stages S0–S11 per `DEVELOPMENT_ROADMAP.md` / `IMPLEMENTATION_PLAN.md`. Release 2 candidates are listed in the roadmap and are not scheduled.

## 5. Decisions outstanding

| ID | Decision | Status | Needed before |
|---|---|---|---|
| OD-1 | First vertical: electrical (recommended, ADR-019) vs HVAC | PROPOSED | S3 (affects UoM/terminology defaults; low switching cost) |
| OD-2 | LLM provider and data-handling terms (ADR-012) | PROPOSED | S6, and before any real document is processed |
| OD-3 | Production PostgreSQL hosting (self-managed vs managed Postgres) (ADR-023) | PROPOSED | S11 (earlier if a pilot needs hosting) |
| OD-4 | Hosting provider and region (ADR-023) | PROPOSED | S11 |
| OD-5 | Release 2 scope and ordering | UNRESOLVED | After S11 |

## 6. Known issues and gaps

| # | Item | Impact | Plan |
|---|---|---|---|
| K-1 | Synthetic data has no scanned, rotated, password-protected, non-English, >60-page or amendment PDFs | AI extraction robustness is untested on those shapes | Add in S6 or replace with real anonymised samples (A-12) |
| K-2 | No real customer documents or exports have been seen | Layout, terminology and edge-case assumptions are unvalidated | Obtain samples from pilot candidates before S3/S6 sign-off |
| K-3 | Market sizing, pricing and cost figures in the docs are estimates, not verified | Business planning only | Validate with customer interviews |
| K-4 | Procrastinate suitability for sync handlers and tenant context is unproven | Job system choice | S2 spike; fallback in ADR-005 |
| K-5 | Legal/commercial review (terms, DPA, data retention) not done | Needed before a paid pilot | Owner to engage counsel |
| K-6 | `minio/minio` images are no longer publicly pullable (Docker Hub: not found; quay.io: 401) | Resolved 2026-11-09: owner approved SeaweedFS for local dev and CI (ADR-017); image added in S2 | Closed |
| K-7 | Starlette deprecates `httpx` for `TestClient` (warning; suggests `httpx2`) | Test-only warning | Swap the test dependency when convenient |
| K-9 | **Temporary in-process background pool** (S1.6, `core/mail.py`): emails go to Mailpit over SMTP from a 2-thread pool fed after commit; the forgot-password lookup/token/audit work runs there too (timing fix). Not durable, no retry; the API refuses to start outside `local`/`ci` | A crash or SMTP failure loses the email (and, for forgot-password, the token); no production email | Replace both with jobs in S2.1 (`docs/reports/S1_KICKOFF_REPORT.md` §5) |
| K-8 | `minimatch` 10.x advisory GHSA-g3hv-75wg-m249 in ESLint tooling (dev only) | None at runtime | npm `overrides` pins minimatch 11.0.5; remove the override once ESLint ships it |

## 7. Risks being watched

Top items only; full list in the analysis and `SECURITY.md` §12.
- **R-1 Dev/prod storage parity:** local/CI use SeaweedFS, production uses the provider chosen in OD-3/OD-4. Before the first pilot, run the same storage test suite (presigned expiry, versioning, SSE, quarantine → clean copy) against the production provider.
- Agreement and remittance formats vary widely; mapping and review UX will matter more than extraction accuracy.
- Provider terms for LLM data processing (OD-2).
- Scope growth: each stage ends with a review and a deliberate "not now" list.

## 8. Stage log

| Stage | Kickoff approved | Completed | Owner sign-off | Notes |
|---|---|---|---|---|
| S0 | 2026-11-09 | 2026-11-09 | 2026-11-09 | Approved |
| S1 | 2026-11-09 | 2026-11-10 | – | Awaiting owner sign-off |
| S2–S11 | – | – | – | Not started |

## 9. Change log

| Date | Change |
|---|---|
| 2026-11-09 | Initial documentation package produced |
| 2026-11-09 | Product named "Veyra Recover", company "Veyra AI" (README, CLAUDE.md, PROJECT_OVERVIEW) |
| 2026-11-09 | Owner approved SeaweedFS for local/CI object storage (ADR-017), with S2 test conditions |
| 2026-11-09 | S0 kickoff approved; doc corrections C-1..C-15 applied (CLAUDE.md, roadmap, schema, API, deployment, tech stack, testing); ADR-027 toolchain versions |
| 2026-11-09 | S0 local Docker verification; `make up` runs db-init first; `make env` Windows interpreter fix; `infra/runbooks/` pointer |
| 2026-11-09 | S0 signed off. S1 kickoff approved with D-1..D-8 (negative-control DB, `cryptography`, local breach list, Postgres rate-limit counters, email-change endpoints, no in-app MFA reset + runbook, per-tenant auth audit rows, `segno`). Owner standing delegation recorded in `CLAUDE.md` §18; reviewer subagent `.claude/agents/reviewer.md` |
| 2026-11-10 | S1 complete: audit shows actor names; runbook operator scripts + dry run (found and fixed a missing EXECUTE grant, migration `20261110_0900`); session-ended message; axe on S1 pages; completion report |
