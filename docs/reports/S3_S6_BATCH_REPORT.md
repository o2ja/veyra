# S3–S6 batch: completion report

## Owner decisions needed
1. **S5 sign-off** (CLAUDE.md §18.4: owner personally): calculation runs merged after two independent financial reviews, no discrepancy. Recommendation: approve.
2. **OD-2 (LLM provider terms + key)**: S6 runs with AI disabled (no live LLM); extraction is tested with recorded fixtures only. Recommendation: decide the provider terms, then give one API key for a live smoke test.
3. **Split-period rebate tiers** (an amendment splitting the measurement period): runs fail closed with 422 `split_period_terms_need_decision`. Recommendation: Release 2, keep the refusal.
4. **Release 2 candidates kept refused by the validator** (`cap_scope=per_agreement`, `tier_metric=quantity`, rebate `claim_window_basis=period_end` for SPA lines): confirm Release 2.
5. Nothing else (no spending, hosting, destructive or security-weakening item).

Date: 2026-11-12. Batch started 2026-11-10 after S2 approval. PRs #2–#14, #16 on `main` (last c41e448).

## 1. What was built
- **S3 data ingestion** (#4 backend, #5 frontend, #13 gaps + integration): master data (manufacturers, customers + aliases,
  products, UoM conversions, branches), import wizard (CSV/XLSX, layouts, mapping, preview, error rows + streamed errors
  CSV, re-import no-op, lineage, unmapped queue), source lines with versioning, per-tenant import concurrency limit,
  formula-safe exports, 1M-row import.
- **S4 agreements** (#6 backend, #9, #11 frontend): draft pipeline (manual, template, AI), one `AgreementValidator`,
  four-eyes approval with `If-Match`, immutable versions with split-supersede, amendments and diff, expiry sweep;
  the amendment rename gap found in e2e is refused (#9).
- **S5 calculations** (#2 engine, #7 runs, #12 D2, #16 UI): pure `rebate_engine` (SPA price difference, percent, flat,
  tiers, caps, claw-back flag, claim window `month_end` SPA / `period_end` rebates), runs as jobs with full traces,
  rebate tracker, draft preview, propose adjustments; D2 widened SPA-line identification.
- **S6 AI extraction** (#3 pure, #8 backend, #10 schema, #16 UI): text/table extraction, deterministic verification
  (two-pass, evidence), AI gateway with restricted role, rate/concurrency limits, audit; typed `ExtractionResult`;
  review UI. No live LLM (OD-2).
- **Frontend for S3–S6**: S3 by Claude; S4 and S5/S6 by an external agent (Antigravity), verified, reviewed and fixed
  by the orchestrator before merge.

## 2. Tests and reviews
- Backend full suite on `main` c41e448: see §5 (run 2026-11-12). Isolation suite + negative control green in CI.
- Golden ground truth: all 23 calculation scenarios + GL-R1a/R1b/R2/R3 match `testdata/generated/ground_truth` (#13).
- CI on every merge: backend, frontend, e2e (Playwright 8 specs + axe), api-contract, secrets, images — green.
- Reviews (independent subagent): S3 security + DB (2 passes), S4 security + DB (2 passes), S6 security + DB
  (3 passes), engine + S5 financial (2 passes each, **no financial discrepancy**), D2 financial (PASS), S4 UI (2),
  S5/S6 UI (2). All blockers/serious findings fixed; dispositions recorded on each PR.
- Performance (`docs/reports/PERFORMANCE_BASELINES.md`): import 1M rows 1,372 s; calc 1M lines 762 s.
  **Caveat:** the calc run ended with `eligible=0` (the file's June invoice dates are outside the claim window at
  today's run date), so the payable path (price difference, caps, rebate tiers, claw-back) was not measured at volume.

## 3. Deviations and decisions taken by the orchestrator (§18.1)
- Claim window: `month_end` = SPA basis, `period_end` = rebates only (owner rule 2026-11-11, interpretation recorded).
- `period_end` of a SPA line = month end of the line's claim date.
- Migration chain re-ordered at merge (extraction before calc_runs).
- CI e2e: per-test client IP is now a Playwright fixture (the old `beforeEach` in `support.ts` bound to one spec
  only, so later specs shared one IP and hit the register limit 5/h/IP).

## 4. Known issues / follow-ups (none blocking)
- **Narrow-run SPA signals:** SPA signals only cover the run's date range while the rebate reads the whole period, so a
  narrow run can leave in-period SPA lines unsignalled (logged by the D2 financial review).
- GL-R5 `mismatch` lines in engine tests carry no SPA result (only the integration test proves exclusion).
- Expiry sweep reschedules itself from inside the job: one dead-lettered run stops that tenant's daily sweeps.
- S6 minors: reaper vs max settings > 1 h; `ProviderBusy` on the last attempt lacks the finished audit; settle-mismatch dead-letters.
- S4 UI minors fixed in #16 (manual-draft defaults stored; read-only focus lands on the field label).
- S5/S6 UI minors: non-ApiError upload failure shows no message; `fields[]` element validation; `window.open` after
  `await`; e2e region-name ambiguity and non-retrying counts; one duplicate results fetch.
- **Endpoint gaps** from the UI agents: file `download-url` is a separate call per file (no inline URL on list rows);
  extraction evidence `boxes` are not exposed for highlighting. Both are UI conveniences.
- Login timing test flaked once in CI (34 %); not weakened.

## 5. Evidence
Full backend suite on `main` c41e448 (2026-11-12, api container, `pytest -p no:randomly`): **1630 passed**, 4 deselected (live-LLM, OD-2), 0 failed, 534.9 s. CI on c41e448: all six jobs green (PR #16 run 43235690790).
