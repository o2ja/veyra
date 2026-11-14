# Release 1 report (build complete, pilot not started)

Date: 2026-11-14. Repository state: `main` after PR #39. Everything below was verified from the repository, CI runs and the review notes named; anything not run is said so.

## Decisions the owner needs to make (5 lines)
1. **D-N11** (blocks task B-1): under the interim rule a later payment of a batch that carries a cross-batch adjustment shows the same 50.00 short on two lines (INV-10020 and CL-4). Pick how such an item is matched. Not chosen by us (financial rule).
2. **OD-3 / OD-4** hosting provider and region (S11.5 deploy), **OD-2** LLM data terms (until then synthetic documents only), **D-N4** retention/deletion (legal review).
3. **Keys:** the Gemini and OpenRouter keys pasted in chat are free-tier test keys: rotate them and buy production keys before any real document is processed.
4. **GitHub:** the repository was made public to get free CI; set it back to private. Dependabot PRs #27 (Python 3.15, Node 26), #28 (React 19, TypeScript 7), #29 (gunicorn 27) are deliberately not merged.
5. **Run the PITR restore drill** (`infra/runbooks/pitr-restore-drill.md`): the scripts have never run against a real Postgres.

## What Release 1 contains (all merged to `main`)
S0 foundation, S1 identity/tenancy/audit, S2 files and job queue, S3 master data and imports, S4 agreements (draft, validator, approval, versions), S5 calculation engine and runs, S6 AI extraction (gateway with Anthropic, Gemini, OpenRouter providers), S7 claims (missed claims, batches, export templates, adjustments), S8 remittance reconciliation, S9 recovery queue and write-off (four-eyes), S10 dashboard, drill-down, trace, audit pack, S11.1 audit-seal chain, S11 ops (prod compose, backup/restore scripts, runbooks), S11.3 security hardening, S11.7 guides. UI for S3-S10 plus cross-cutting QA specs.

## Verification evidence
- **Backend:** 2,415 tests pass on `main`-equivalent CI (GitHub Actions, PR #36 run), none skipped; tenant-isolation suite 436 passed; RLS-off negative control: 217 RLS-dependent tests detect the breach.
- **Browser:** 25 Playwright specs on `main` (every stage UI plus QA/axe specs); all pass on the live stack and in CI. axe checks cover every S3-S10 page including empty, not-found and 403 states. No manual keyboard / screen-reader pass was done.
- **CI:** api-contract, backend, frontend, e2e, images, secrets pass on every merged PR (jobs skipped only by path filter on docs/infra-only changes).
- **Ground truth:** calculation, reconciliation and recovery figures were re-derived independently in the reviews and in the e2e specs from `testdata/generated/ground_truth` (e.g. SC-07b 2,080.00; SC-22 660.00; short-paid 150.00; unmatched payment 75.00).
- **Performance (S11.2, 100k and 250k rows, one run each):** import ~820 rows/s, calculation 1,176-1,355 lines/s, both matching ground truth exactly. Extrapolated to 1M: import ~20 min (target 30), calculation ~12.3 min (target 15). Not measured at 1M or 5M (decision D-N9). See `PERFORMANCE_BASELINES.md`.
- **Dependencies:** pip-audit and npm audit report 0 vulnerabilities; licence check no blockers (`DEPENDENCY_AUDIT_2026-11.md`).
- **Security review of S7-S10 (ASVS L2 delta):** no cross-tenant leak, authorisation bypass, SQL injection or formula-injection hole found. One medium finding (no throttle on job-creating endpoints) and the NUL-in-free-text finding were fixed (PR #38). Minors left open, see below.

## Known issues and limits
- **Performance:** `/missed-claims` is not index-bounded: 1.5 s at 100k lines, 2.0 s at 250k (target 500 ms); needs a query/index rework before a large pilot. Import memory grows ~0.9 KB per row (whole-file duplicate-detection state in `ingestion/validators.py`); fine to ~1M rows (~1.2 GB), review before 5M.
- **Security minors (not fixed):** missed-claims export is a state-changing GET (spec allows all roles); export files can be downloaded by any `data.read` role; the write-off approver's reason is not bound to the proposal; notes/attachments can still be added to closed cases; `GET /remittances/{id}/extraction` needs `remittance.reconcile` where the spec says all roles; a trigger error on claim lines can surface as `already_claimed`.
- **Not verified:** reconciliation allocations summed across several remittances may exceed one claim line (shows as an `overpaid` finding by design; not exercised); PITR/WAL scripts never run; UI e2e does not apply a manual match, does not assert the Unpaid finding (not due until the payment lag passes) and does not exercise ADJ-2026-0001 (the e2e flow nets the return at calculation).
- **Not done (owner or later):** S11.5 deployment and staging, ZAP baseline and external pen test (need staging), S11.8 pilot readiness review, S6.8 agreed LLM quality bar, release-2 items (split-period tiers, three validator refusals).
- **CI flake root causes fixed:** the sign-in raced the accept-invitation request (all specs now wait for the login redirect); a positional locator in the adjustments spec shifted with cached rows.

## How to run and verify
`make up`, `make ci-local` (six jobs) or push a ready PR (GitHub Actions). AI: set `AI_PROVIDER` and the matching key in the local `.env` only; synthetic documents only until OD-2.
