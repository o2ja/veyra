# S3–S6 batch: completion report

## Owner decisions needed
1. **S5 sign-off** (CLAUDE.md §18.4: owner personally): calculation runs merged after two independent financial reviews, no discrepancy. Recommendation: approve.
2. **OD-2 (LLM provider terms + key) and the S6.8 extraction quality bar**: S6 runs with AI disabled; extraction is tested with recorded fixtures only, so the quality bar could not be agreed. Recommendation: decide the provider terms, give one key for a live run, then agree the bar from that run's numbers.
3. **Split-period rebate tiers** (an amendment splitting the measurement period): runs fail closed with 422 `split_period_terms_need_decision`. Recommendation: Release 2, keep the refusal.
4. **Release 2 candidates kept refused by the validator** (`cap_scope=per_agreement`, `tier_metric=quantity`, rebate `claim_window_basis=period_end` for SPA lines): confirm Release 2.
5. **OD-1 (electrical as first vertical)** is still PROPOSED in PROJECT_STATUS although S3 test data and master data assume it. Recommendation: confirm.

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
- Golden ground truth: all 23 calculation scenarios + GL-R1a/R1b/R2/R3 match `testdata/generated/ground_truth`
  (`docs/reports/notes/c1-s5-runs.md`, #13). Caveat: GL-R5 `mismatch` lines carry no SPA result in the engine tests
  (only the integration test proves exclusion). "No financial discrepancy" covers computed values; the split-period
  rule was left to the owner (decision 3).
- CI on every merge: backend, frontend, e2e (Playwright + axe), api-contract, secrets, images — green (run ids on
  each PR). The 8-spec e2e suite passes in CI in one run (43235690790); locally it is run in ≤4-spec batches with a
  dev rate-limit reset in between (the external agent's own full run failed on that limit).
- Reviews (independent subagent; results in `docs/reports/notes/overnight-log.md` and as comments on each PR): S3 security + DB (2 passes), S4 security + DB (2 passes), S6 security + DB
  (3 passes), engine + S5 financial (2 passes each, **no financial discrepancy**), D2 financial (PASS), S4 UI (2),
  S5/S6 UI (2). All blockers/serious findings fixed; dispositions recorded on each PR.
- Performance (`docs/reports/PERFORMANCE_BASELINES.md`): import 1M rows 1,372 s; calc 1M lines 762 s. Caveats:
  single run on a shared, loaded VM (reference-VPS run open); 27 % (import) and 21 % (calc) slower than the earlier
  baselines, unexplained; within NFR-04 only by a margin on a noisy run. The calc run ended with `eligible=0` (June
  invoice dates outside the claim window at today's run date), so the payable path (price difference, caps, rebate
  tiers, claw-back) and list p95 on a 5M-line tenant were not measured. The 1M-row file (163 MB) exceeds the 50 MB
  upload cap: the test seeds the file object directly, so 1M rows is a pipeline capacity, not a single-upload one.

## 3. Deviations and decisions taken by the orchestrator (§18.1)
- Claim window: `month_end` = SPA basis, `period_end` = rebates only (owner rule 2026-11-11, interpretation recorded).
- `period_end` of a SPA line = month end of the line's claim date.
- Migration chain re-ordered at merge (extraction before calc_runs).
- CI e2e: per-test client IP is now a Playwright fixture (the old `beforeEach` in `support.ts` bound to one spec
  only, so later specs shared one IP and hit the register limit 5/h/IP).
- Builder deviations from approved docs (details and doc updates in the notes files):
  S4 exclusion constraint covers active **and** expired versions (doc: active), split-supersede generalised beyond
  §9.2, drafts stored even when invalid, validator rules beyond §3.3, "by template" reinterpreted
  (`b1-s4-backend.md` deviations 1, 6, 8, 10, 13); AI_PROCESSING §3.5 chunking not implemented (documents over 400k
  characters go to manual entry) and per-field acknowledgement not enforced server-side (`c2-s6-backend.md`);
  INV-30004 unknown product is an error row, not only an unmapped-queue item (`a1-s3-backend.md`).

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
- Claw-back is only flagged until S7; S5.3 recalculation proposals against frozen claims and the S3.3
  `source_changed` hook were stubs until S7.4 (`s7-adjustments`, in review).
- Sales-basis rebates with `spa_lines ≠ include` starting before `date_from` are refused (422 `scope_too_narrow`).
- The F7 settings data migration is not audited (`c1-s5-runs.md`).
- No per-job concurrency cap in the worker; no extra rate limit on the new S6 endpoints beyond per-workspace limits.
- No Playwright step for "Suggest with AI" (AI disabled in CI).
- `IMPLEMENTATION_PLAN.md` S3–S6 checkboxes are not yet ticked; `CODE_REVIEW_CHECKLIST.md` not filled per stage.
  Manual verification was done through the e2e flows and the orchestrator's browser checks of S4 UI only. To do in
  the S11 release pass.
- Login timing test flaked once in CI (34 %); not weakened. Action: S11 — make the measurement robust (more samples).

## 5. Evidence
Full backend suite on `main` c41e448 (2026-11-12, api container, `pytest -p no:randomly`): **1630 passed**, 4 deselected (live-LLM, OD-2), 0 failed, 534.9 s. CI on c41e448: all six jobs green (PR #16 run 43235690790).
