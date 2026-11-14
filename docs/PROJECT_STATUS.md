# Project Status

> Living document. Updated at the end of every stage and whenever a decision, risk or known issue changes. Last updated: 2026-11-14.

## 1. Current position

| Item | State |
|---|---|
| Phase | **Release 1 build** |
| Current stage | **Release 1 build: backend S0-S10 and UI S3-S10 merged to `main`** (Playwright specs under `frontend/e2e` for each UI). Remaining S11 items: S11.2 performance (the 1M-line run never completed), S11.4 accessibility pass (axe covers the S1-S3 routes on `main`; cross-cutting axe specs are in open PR #34), S11.5 deployment, S11.6 WAL/PITR (only a local dump restore drill exists), S11.8 pilot review; see §3 |
| Application code | S0 skeleton; S1 identity, tenancy, audit, auth/MFA/sessions/CSRF, isolation suite; S2 files + job queue; S3 ingestion; S4 agreements; S5 calculation runs; S6 AI extraction; S7 claims and adjustments; S8 reconciliation; S9 recovery; S10 reporting; S11.1 audit seal, S11 ops, S11.3 hardening, S11.6 restore drill and S11.7 guides (partial S11); UI S3-S10 |
| AI provider | Anthropic, Gemini and OpenRouter are selectable by `AI_PROVIDER` (#8, #26). Synthetic documents only until OD-2 is decided; live calls use no real customer data |
| Synthetic test data | Generated and self-checked (`testdata/`) |

## 2. Completed

- [x] Product, architecture and technical analysis (earlier phase)
- [x] Owner decisions recorded (local PostgreSQL, no BaaS, React SPA + separate backend, manual + AI agreements on one pipeline, staged delivery with approvals)
- [x] Documentation package (`docs/`, `CLAUDE.md`, `README.md`)
- [x] Synthetic dataset generator and ground truth (`testdata/generate.py`)
- [x] S0 Foundation and tooling (signed off 2026-11-09)
- [x] S1 and S2 (signed off 2026-11-10); 1M-row baselines in `reports/PERFORMANCE_BASELINES.md`
- [x] S3-S6 backends and UIs merged to `main` (#2-#13, #16); S5 signed off by the owner 2026-11-12
- [x] S7-S10 backends merged in integration PR #25 (after #15 S7 core, #17 S8 matching); UIs merged: S7 #30, S8 #31, S9 #32, S10 #33 (each with its Playwright spec)
- [x] S11 parts merged: S11.1 audit seal (#21), ops/backup/runbooks/dependency audit (#18), restore drill (local dump) and guides (#20), S11.3 security hardening (#22), ESLint security plugin and weekly Dependabot (#24), `make ci-local` gate (#23), Gemini and OpenRouter providers (#26)
- [x] Independent check of every worked example in `BUSINESS_LOGIC.md` (`testdata/verify_business_logic_examples.py`, passes)

## 3. In progress

S1 signed off by the owner 2026-11-10. Evidence and open points: `docs/reports/S1_COMPLETION_REPORT.md`. CI green on `main` (jobs: backend incl. isolation negative control, frontend, e2e (Playwright W1–W2 + axe), api-contract, secrets, images).

**CI and merging (owner, 2026-11-13):** GitHub Actions is unpaid; the merge gate is `make ci-local` (6/6 on the exact commit, posted as a PR comment with the SHA) after merging latest `main`, plus the required reviews; nothing skipped or failing. Workflow runs only on ready-for-review PRs and pushes to `main`, with path filters; WIP PRs are drafts. At most 2 agents; full suite + Playwright once per merge; never two full suites at once. Full text: `CLAUDE.md` §19.

**Resume point (2026-11-14):** Release 1 build merged (backend S0-S10, UI S3-S10). Remaining work:
- S11.2 performance: the 1M-line run never completed; measure 100k/250k per D-N9.
- S11.4 accessibility: merge PR #34 (cross-cutting axe, empty/not-found/403 states) once gated, then axe-check every route; not complete today.
- S11.5 deployment, S11.6 WAL/PITR (only the local dump drill, `docs/reports/RESTORE_DRILL_2026-11-13.md`), S11.8 pilot readiness review: need OD-3/OD-4.
- Owner decisions open: D-N11 (reconciling a payment against the carrying batch of an adjustment), D-N4 (retention/deletion path, legal review; only the dry-run exists), OD-2, OD-3, OD-4 (see §5).
- Dependabot PRs #27-#29 are open on purpose, not merged for Release 1: #27 moves Python 3.14->3.15 and Node 24->26 (contradicts the ADR-027 pins); #28 moves React 18->19 and TypeScript 5->7 (contradicts `TECH_STACK.md`); #29 bundles a gunicorn 26->27 major among minors. See `docs/reports/DEPENDENCY_AUDIT_2026-11.md`.

### S3–S6 batch (complete 2026-11-12; history)

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
- **S3–S6 batch complete (2026-11-12):** #16 S5/S6 UI merged (c41e448); report `docs/reports/S3_S6_BATCH_REPORT.md`
  (full suite 1630 passed; S5 sign-off is the owner's).
- **Earlier resume point (2026-11-12 afternoon, superseded):** PR #15 S7 core (gated on F2 `s7-adjustments`, financial review running);
  PR #17 S8 matching (financial review: no discrepancy; findings fixed); PR #18 S11 ops (CI + security review);
  S7 UI with Antigravity on `ui/s7` (brief `docs/reports/notes/ui-brief-s7.md` on that branch).
- Earlier (2026-11-12 midday): merged #12 D2 (financial review PASS), #13 S3 gaps + end-to-end
  integration (all 23 scenarios + GL-R1a/R1b/R2/R3 match ground truth), #14 perf baselines
  (`docs/reports/PERFORMANCE_BASELINES.md`: import 1M 1372 s, calc 1M 762 s, eligible=0 caveat). S5/S6 UI
  (`ui/s5-s6`): batch 1 review FAIL → batch 2 fixes with Antigravity (`ui-brief-s5-s6-fixes.md`). Running: F1 `s7-claims`
  (S7.1–S7.3), G1 `s8-matching` (S8.1–S8.2 pure), H1 `s11-ops` (deploy config, CF-Connecting-IP, backup/restore,
  runbooks, dependency audit). Next: S7.4 adjustments after F1; S7 UI brief for Antigravity; S3–S6 batch report
  after the UI merges. Deadline 2026-11-15.
- Claim window (owner 2026-11-11): `month_end` for SPA lines (last day of the claim-date month), `period_end` for
  rebates only (measurement period end); replaces the earlier orchestrator definition.
- Open engine rules (raise instead of guessing): `cap_scope=per_agreement`, `tier_metric=quantity`,
  `claim_window_basis=period_end`.

## 4. Pending

S11 remainder (see §3); Release 2 per `DEVELOPMENT_ROADMAP.md` / `IMPLEMENTATION_PLAN.md`. NFR-04 evidence (shared dev VM, one run each): 1M-row import 22.9 min (target < 30), 1M-line calculation 12.7 min (target < 15), both on a file where no line is eligible; list-endpoint p95 on 5M lines not measured. Release 2 candidates are listed in the roadmap and are not scheduled.

## 5. Decisions outstanding

| ID | Decision | Status | Needed before |
|---|---|---|---|
| OD-1 | First vertical: electrical (recommended, ADR-019) vs HVAC | APPROVED (owner 2026-11-12) | S3 (affects UoM/terminology defaults; low switching cost) |
| OD-2 | LLM provider and data-handling terms (ADR-012) | DEFERRED (owner 2026-11-12: not now; S6/S8 use recorded fixtures) | S6, and before any real document is processed |
| OD-3 | Production PostgreSQL hosting (self-managed vs managed Postgres) (ADR-023) — object storage decided 2026-11-13: managed S3-compatible (ADR-017, `PRODUCTION_PLAN.md`); Postgres hosting/provider still open | PROPOSED | S11 (earlier if a pilot needs hosting) |
| OD-4 | Hosting provider and region (ADR-023) — object storage decided 2026-11-13: managed S3-compatible (ADR-017, `PRODUCTION_PLAN.md`); Postgres hosting/provider still open | PROPOSED | S11 |
| OD-5 | Release 2 scope and ordering | PARTIAL (owner 2026-11-12: 3 validator refusals + split-period tiers = Release 2) | After S11 |

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
| K-10 | Presigned downloads without `X-Content-Type-Options: nosniff` | — | **Closed in S11.3**: the storage-host block of `infra/caddy/Caddyfile` sets nosniff, a CSP and HSTS (asserted by `tests/security/test_s11_hardening.py`; not yet verified on a live host, OD-3/OD-4) |
| K-11 | Finished `jobs` rows are never purged; no external alert on dead letters | Table growth; failures seen only on the Jobs page and in logs | Retention sweep and alerting with S9 operations |
| K-12 | Email delivery is at-least-once (a job can send twice if its commit fails after SMTP accepted) | Rare duplicate email; for token emails the later link wins | Accepted; documented in ARCHITECTURE.md §6 |
| K-8 | `minimatch` 10.x advisory GHSA-g3hv-75wg-m249 in ESLint tooling (dev only) | None at runtime | npm `overrides` pins minimatch 11.0.5; remove the override once ESLint ships it |

## 7. Risks being watched

Top items only; full list in the analysis and `SECURITY.md` §12.
- **R-2 Restore drill (2026-11-13, local dev stack, who: builder L1):** backup 8 s, restore 16 s, row counts of all tables, alembic head, 100/100 object SHA-256 all equal; isolation + golden suites 380 passed against a fresh test DB. WAL/PITR NOT set up or drilled; not run against production-sized data or the restored DB itself. Details: `docs/reports/RESTORE_DRILL_2026-11-13.md`.
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
| S3 | – | 2026-11-10 (backend #4, UI #5; gaps #13 2026-11-12) | - | Merged |
| S4 | – | 2026-11-12 (backend #6 2026-11-11, UI #11) | - | Merged |
| S5 | – | 2026-11-12 (backend #7 2026-11-11, UI #16) | 2026-11-12 | Approved |
| S6 | – | 2026-11-12 (backend #8 2026-11-11, UI #16); providers #26 2026-11-14 | - | Merged; OD-2 deferred, synthetic documents only |
| S7 | – | 2026-11-14 (#15, #25, UI #30) | - | Merged |
| S8 | – | 2026-11-14 (#17 2026-11-12, #25, UI #31) | - | Merged |
| S9 | – | 2026-11-14 (#25, UI #32) | - | Merged |
| S10 | – | 2026-11-14 (#25, UI #33) | - | Merged |
| S11 | – | – | - | Partial: S11.1 #21, S11.3 #22, S11.6 local drill + S11.7 #20, ops #18; S11.2/S11.4/S11.5/S11.6 PITR/S11.8 open |

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
| 2026-11-12 | Owner decisions: S5 signed off; OD-1 approved; OD-2 deferred (recorded fixtures); 3 validator refusals + split-period tiers moved to Release 2; `cannot_be_net_claimed` adjustments stay `proposed` and batches <= 0 are refused unless `allow_negative_claims`; unpaid line without rejection code = `short_paid_unexplained` (BUSINESS_LOGIC §10.3) |
| 2026-11-13 | Owner rules: `make ci-local` merge gate, draft-PR/path-filtered workflow, 2-agent limit (`CLAUDE.md` §19). Owner decisions D-N1/D-N3/D-N7 (an adjustment belongs to the batch that carries it; frozen claim lines reconcile at their frozen amount; no return_reversal/agreement_change for a return netted into a frozen claim), D-N2 (unpaid deadline = submission date + dispute window), D-N5 (closed cases immutable; separate "recovered after write-off" S10 figure), D-N6 (dashboard definitions; one at-risk definition; month buckets in tenant business time zone; XLSX audit pack for Release 1, PDF Release 1.1), D-N4 deferred (legal review; dry-run only), D-N8 (ESLint security plugin + weekly grouped Dependabot approved; ZAP/pen test wait for staging), D-N9 (no 1M run locally; measure 100k/250k import memory, growth = streaming bug) |
| 2026-11-13 | Owner decision D-N10: a return netted into a frozen claim and later reduced or voided → claimed original evaluated on q − min(netted, current returns); shortfall proposed as `agreement_change`, carried by a later batch |
| 2026-11-14 | S7-S10 integrated (#25) and UIs S7-S10 merged (#30-#33); S11.1 (#21), S11.3 (#22), D-N8 tooling (#24), Gemini and OpenRouter providers (#26) merged; status docs and `IMPLEMENTATION_PLAN.md` ticks brought up to date; dependency/licence audit `docs/reports/DEPENDENCY_AUDIT_2026-11.md`; Dependabot #27-#29 decided not merged for Release 1. AI providers: Anthropic, Gemini, OpenRouter selectable by `AI_PROVIDER`, synthetic documents only until OD-2 |
