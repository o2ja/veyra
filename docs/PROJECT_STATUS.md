# Project Status

> Living document. Updated at the end of every stage and whenever a decision, risk or known issue changes. Last updated: 2026-11-09.

## 1. Current position

| Item | State |
|---|---|
| Phase | **Documentation complete, awaiting owner review and approval** |
| Current stage | None started. Next stage: **S0 Foundation and tooling** (needs kickoff report and owner approval) |
| Application code | **None written** (by instruction) |
| Synthetic test data | Generated and self-checked (`testdata/`) |

## 2. Completed

- [x] Product, architecture and technical analysis (earlier phase)
- [x] Owner decisions recorded (local PostgreSQL, no BaaS, React SPA + separate backend, manual + AI agreements on one pipeline, staged delivery with approvals)
- [x] Documentation package (`docs/`, `CLAUDE.md`, `README.md`)
- [x] Synthetic dataset generator and ground truth (`testdata/generate.py`)
- [x] Independent check of every worked example in `BUSINESS_LOGIC.md` (`testdata/verify_business_logic_examples.py`, passes)

## 3. In progress

Nothing. Waiting for owner review of the documentation.

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

## 7. Risks being watched

Top items only; full list in the analysis and `SECURITY.md` §12.
- Agreement and remittance formats vary widely; mapping and review UX will matter more than extraction accuracy.
- Provider terms for LLM data processing (OD-2).
- Scope growth: each stage ends with a review and a deliberate "not now" list.

## 8. Stage log

| Stage | Kickoff approved | Completed | Owner sign-off | Notes |
|---|---|---|---|---|
| S0 | – | – | – | Not started |
| S1–S11 | – | – | – | Not started |

## 9. Change log

| Date | Change |
|---|---|
| 2026-11-09 | Initial documentation package produced |
| 2026-11-09 | Product named "Veyra Recover", company "Veyra AI" (README, CLAUDE.md, PROJECT_OVERVIEW) |
