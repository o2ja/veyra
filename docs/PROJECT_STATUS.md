# Project Status

> Living document. Updated at the end of every stage and whenever a decision, risk or known issue changes. Last updated: 2026-11-11.

## 1. Current position

| Item | State |
|---|---|
| Phase | **Release 1 build** |
| Current stage | **S3–S6 batch in progress** (S2 approved 2026-11-10); see §3 |
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

S1 signed off by the owner 2026-11-10. Evidence and open points: `docs/reports/S1_COMPLETION_REPORT.md`. CI green on `main` (jobs: backend incl. isolation negative control, frontend, e2e (Playwright W1–W2 + axe), api-contract, secrets, images).

### S3–S6 batch (in progress, started 2026-11-10; S2 approved)

Multi-agent waves (worktree branches, orchestrator merges). Resume point:
- Wave A merged: `s6-pure` (#3), `s5-engine` (#2), `s3-backend` (#4, 2026-11-11: security + DB reviews 2nd pass PASS;
  1M-row perf 1077 s total / 929 rows/s, peak RSS 1.18 GB, no Postgres crash). S3 frontend (#5) merged.
  Main after S3: full suite 1168 passed; isolation + schema lint + negative control OK (121 RLS-dependent).
- Wave B: `s4-backend` MERGED (#6, 2026-11-11): security + DB reviews, 2nd pass PASS (fixes: lock+refresh before
  validating approve, set-based expiry sweep, line_number keyset, global JSON body cap incl. CL+TE refusal,
  approve If-Match, draft `Payload` schema published in OpenAPI). Main after S4: full suite 1373 passed; isolation
  290 + negative control OK (147 RLS-dependent).
- S4 frontend handed to an external agent (Antigravity/Gemini) on branch `ui/s4` (worktree `D:eyraeyra-ui-s4`):
  brief `docs/reports/notes/ui-brief-s4.md`, prompt `antigravity-prompt-s4.md`, report expected at
  `ui-report-s4.md`. Orchestrator verifies + reviews before merge. (S3 frontend was already merged in #5.)
- **Resume point (2026-11-12 early):** done: S4 backend #6, S6 backend #8, S5 runs #7 merged (main 5fbce94: 1623
  passed, isolation 332, negative control OK). Next: S4 UI PR #11 (re-review) and #10 (ExtractionResult schema) →
  S5/S6 UI hand-off briefs → integration, perf, docs, QA pass → batch report. Owner decisions D1–D3: overnight log.
- Claim window (owner 2026-11-11): `month_end` for SPA lines (last day of the claim-date month), `period_end` for
  rebates only (measurement period end); replaces the earlier orchestrator definition.
- Open engine rules (raise instead of guessing): `cap_scope=per_agreement`, `tier_metric=quantity`,
  `claim_window_basis=period_end`.

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
| K-4 | Procrastinate suitability | — | **Closed in S2**: spike showed no enqueue inside our transaction; in-house queue per ADR-005 fallback |
| K-5 | Legal/commercial review (terms, DPA, data retention) not done | Needed before a paid pilot | Owner to engage counsel |
| K-6 | `minio/minio` images are no longer publicly pullable (Docker Hub: not found; quay.io: 401) | Resolved 2026-11-09: owner approved SeaweedFS for local dev and CI (ADR-017); image added in S2 | Closed |
| K-7 | Starlette deprecates `httpx` for `TestClient` (warning; suggests `httpx2`) | Test-only warning | Swap the test dependency when convenient |
| K-9 | Temporary in-process background pool (S1) | — | **Closed in S2**: all email and the forgot-password work run as queued jobs with retries and a visible dead-letter state |
| K-10 | Presigned downloads come straight from SeaweedFS without `X-Content-Type-Options: nosniff` (SECURITY.md §4 deviation) | Low: `Content-Disposition: attachment` + explicit type | Header at a proxy in front of storage with the hosting choice (OD-3/OD-4) |
| K-11 | Finished `jobs` rows are never purged; no external alert on dead letters | Table growth; failures seen only on the Jobs page and in logs | Retention sweep and alerting with S9 operations |
| K-12 | Email delivery is at-least-once (a job can send twice if its commit fails after SMTP accepted) | Rare duplicate email; for token emails the later link wins | Accepted; documented in ARCHITECTURE.md §6 |
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
| S1 | 2026-11-09 | 2026-11-10 | 2026-11-10 | Approved |
| S2 | 2026-11-10 | 2026-11-10 | – | Complete, awaiting owner review |
| S3–S11 | – | – | – | Not started |

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
| 2026-11-10 | S2 complete: job queue (ADR-005 fallback), worker, files module (SeaweedFS + ClamAV, quarantine → clean), Files/Jobs pages; two independent reviews (8 serious findings fixed); K-4, K-9 closed; K-10..K-12 opened |
