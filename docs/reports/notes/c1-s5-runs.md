# c1-s5-runs notes (S5.3-S5.5, S5.7; branch `s5-runs`)

## A. Owner decisions applied (2026-11-11)

- R1 = 10,150.00 and SPA-line identification = SPA reference OR eligible (BUSINESS_LOGIC §5.3/§5.4): already in the
  engine; the run feeds the second signal from the SPA results of the same run.
- `cap_scope = per_agreement`, `tier_metric = quantity`: rejected by the S4 validator (code
  `not_supported_until_release_2`, tested again through the draft API and the preview), by the engine
  (`UnsupportedRuleError`, existing unit tests) and, as a run/preview problem response (422/failed run), never a 500.
- `claim_window_basis = agreement_end`: not in the settings Literal -> 422 on `PUT /manufacturers/{id}/settings`
  (tested); the engine raises `EngineRuleError` for any unknown basis.
- Claim window basis split (engine change in `rebate_engine`, allowed by the brief): SPA lines use `invoice_date`,
  `ship_date` or `month_end` (old SPA `period_end` renamed; trace key `period_end` -> `month_end`); `period_end` on an SPA
  line raises `EngineRuleError`; rebates: `deadlines.rebate_deadline(period_end, days)` (period end + days).
  Hand-derived tests: D3 (2026-03-10 + 60 d, month end -> 2026-05-30 inclusive; engine unit and DB end-to-end with a
  clock moved across local midnight), leap year (2028-02-10 -> 2028-02-29 -> 2028-04-29), rebate 2026-12-31 + 60 d =
  2027-03-01, 2027-12-31 + 60 d = 2028-02-29. No financial discrepancy between engine, BUSINESS_LOGIC and ground truth
  was found: all 23 scenarios, SC-14b/c, the 2,000-line volume file and the VOLT/NWND rebate cases match end to end.

## B. Doc deltas (for the orchestrator to merge; BUSINESS_LOGIC.md is already edited)

- **BUSINESS_LOGIC.md** (edited here): §4.0 gate 9, §12 claim-deadline row (SPA bases; `period_end` rebates only;
  `agreement_end` rejected), Example D3 renamed month end (+ leap-year deadline), new Example D4 (rebate deadline), GL-D3
  name, §5.3 ledger paragraph (completed runs only; deadline stored; split-supersede choice).
- **DATABASE_SCHEMA.md** §2.x `manufacturer_settings.claim_window_basis`: `invoice_date`, `ship_date`, `month_end`
  (SPA), `period_end` (rebates only). §3.7: `calculation_runs` adds `scope_hash`, `run_date`; `calculation_results` adds
  `excess_quantity`, `flags text[]`; `rebate_accrual_entries` adds `claim_deadline`, `flags`, `inputs_hash`;
  `claim_adjustments` adds `run_id` (nullable `claim_line_id`/`variance_case_id` without FK until S7/S9). Partial unique
  index `uq_calculation_runs_active_scope (tenant_id, scope_hash) WHERE status IN ('queued','running')`.
- **API_SPECIFICATION.md** §2.5/2.6: `preview-calculation` is **POST** `/agreement-drafts/{id}/preview-calculation`
  (body `{limit?}` <= 200; read-only; 422 with the validator errors when the draft is invalid or uses an engine-rejected
  rule). `POST /calculation-runs` also answers 422 `validation_error` with details `{field, code, message}` for
  `engine_rejected`, `not_supported_until_release_2`, `scope_too_narrow`, `in_future`, `not_found`; `run_in_progress` (409)
  when the same scope is queued/running. Results/accruals of a run that is not `completed` -> 409 `invalid_state`.
  Added query filter set as built: `status`, `exception_code`, `manufacturer_id`, `agreement_id`; accruals:
  `agreement_id`, `run_id`. `GET /rebate-tracker` returns `{data: [...]}` (latest completed entry per agreement and period
  with tiers, next tier and amount to go).
- **IMPLEMENTATION_PLAN.md** (orchestrator ticks): S5.3, S5.4, S5.5, S5.7 done here; S5.6 frontend not done.
- **ARCHITECTURE_DECISIONS** (suggested ADR): partial-run invisibility is by reader filter (`status = completed`) because
  results are append-only and cannot be deleted; failed runs keep their rows as evidence.

## C. Decisions and interpretations

1. Run scope: `{manufacturer_ids[] (empty = all, plus lines without a known manufacturer), date_from, date_to, as_of?}`.
   `as_of` (default now, never in the future) pins agreement-version visibility; `run_date` = business date of `as_of`
   (claim-window gate); `data_cutoff` = creation instant. Rebate accrual `as_of_date` = `date_to`.
2. Visibility of versions at `as_of`: activated at or before it and (`active`/`expired`, or `superseded` after it, or
   `cancelled` after it). Source lines: current lines of import batches `completed`/`completed_with_errors` with
   `completed_at <= data_cutoff` (superseded-after-cutoff lines are NOT reconstructed; a rerun after a source change has a
   different `input_checksum` by design).
3. Execution: one engine call per manufacturer (plus the no-manufacturer group); SPA before rebates; results inserted in
   chunks of 1,000, each its own transaction; the job renews its lease after every chunk. Failure -> run `failed` with
   `error.details[]`, committed chunks stay (append-only) but are invisible to every reader. Retries only happen while
   another run holds the tenant advisory lock (`RunInProgress`, 409/retry).
4. Preflight (create and execute): unsupported stored rules, `period_end` basis for a manufacturer with SPA agreements,
   and a sales-basis rebate with `spa_lines != include` whose period starts before `date_from` (the SPA-line signal would be
   incomplete) are refused with 422 / failed run. Chosen over silently computing a wrong qualifying volume.
5. Split-supersede for rebates: several versions of one agreement for the same period -> the version with the latest
   `valid_from` in force at the run's end date governs the cumulative figure (ledger key = agreement + period start).
6. Items with a `customer_id` in `agreement_version_items` are ignored by the loader (the payload has no per-customer
   items; engine items are product-only, a2-engine note C16).
7. Frozen claims: `claims.frozen.FrozenClaimSource` (`prior_claims`, `consumption`, `frozen_claims`); default
   `NoFrozenClaims` (no claim lines before S7). Tests use a source built from `claim_batches_fixture.json` and
   `expected_calculation.json` (SC-09r adjustment, proposals).
8. Negative rebate delta (claw-back): flagged `rebate_clawback` on the accrual entry. Creating the `rebate_clawback`
   adjustment needs a claim line (S7); not created here.
9. Unit amounts are stored at NUMERIC(18,6) like the schema says; the trace keeps full precision.
10. `claim_adjustments` UPDATE is limited to the approval columns by trigger + grants; transitions are enforced.

## D. Files outside `modules/claims` that were touched (allowed list)

- Router/worker registration: `api/app.py` (`MODULE_ROUTERS`), `workers/__main__.py` (import). Permission catalog: none
  needed (`calculation.run`, `data.read` exist).
- `importlinter.ini`: `claims-internals` protected contract (the module-boundary meta test requires it).
- `migrations/env.py`: import of the claims models; migration `20261114_0900_calc_runs` (down_revision
  `20261112_0900_agreements`; also relaxes the `claim_window_basis` CHECK to add `month_end`).
- `modules/audit/events.py`: actions `calculation_run.created/completed/failed/adjustments_proposed` and the
  `calculation_run` field allow-list.
- `modules/masterdata/schemas.py`: `claim_window_basis` Literal gains `month_end` (no migration-free change is possible:
  the CHECK is relaxed in the S5 migration above). The frontend option list in `manufacturers-pages.tsx` still offers
  "Period end" for SPA use: the frontend owner should show "Month end (SPA lines)" and, for rebates, "Period end".
- `rebate_engine/{spa,models,deadlines}.py` + tests (claim-window split, brief-approved).
- `frontend/src/api/{openapi.json,schema.d.ts}` regenerated (CI `check-client`); schema names chosen not to collide
  with existing ones (`RunJobRef`, `DraftPreview*`, `AccrualTierOut`).
- Cross-module access: loaders read other modules' tables with read-only SQL (bulk reads; documented in
  `loaders.py`); `preview` reads `agreement_drafts` by SQL and passes a duck-typed draft to `agreements.validate`
  (its `Draftish` is not exported). Both are candidates for small public functions in those modules.

## E. Interfaces other agents depend on

- `rebate.modules.claims`: `router`, `frozen` (`FrozenClaimSource`, `FrozenClaim`, module variable `frozen.source` that
  S7 replaces).
- Tables: `calculation_runs`, `calculation_results`, `rebate_accrual_entries`, `claim_adjustments` (see migration).
  S7 `claim_lines.calculation_result_id` -> `calculation_results (tenant_id, id)`; `claim_adjustments.claim_line_id`
  FK is S7's job.
- Endpoints: `POST/GET /calculation-runs`, `GET /calculation-runs/{id}`, `/results`, `GET /calculation-results/{id}`,
  `GET /rebate-accruals`, `GET /rebate-tracker`, `POST /calculation-runs/{id}/propose-adjustments`,
  `POST /agreement-drafts/{id}/preview-calculation`.

## F. Not done / limitations

- S5.6 (frontend) is not part of this branch. The "missed claims" endpoints (`/missed-claims`) are S7/S10 scope.
- Memory: one engine call per manufacturer holds that manufacturer's lines and traces; see the performance numbers below.
- Rebate preview with sales basis counts only lines with an SPA reference as SPA lines (no SPA results exist for a draft).

## G. Performance baseline (1M lines)

`pytest -m performance tests/integration/test_calculation_performance.py` (file = the 2,000-line SYNTHETIC volume file
replicated 500 times with unique invoice numbers, because the generator needs `reportlab`, which is not installed in the
dev image; real import pipeline). Single calculation run over 1,000,000 sales lines, one manufacturer, SPA-VOLT-2026-884:
**630.5 s = 1,586 lines/s**; process peak RSS **1,253 MB** (the import phase of the same process set that peak, so the
calculation stayed below it). Memory is bounded by `runs.WINDOW_LINES` (50,000 lines per engine call, whole invoice dates,
cap state carried between windows; tested identical to the single-window result). An earlier unwindowed attempt
(one engine call per manufacturer) died without output at 1M lines (about 7 KB per line, ~7 GB: out of memory in the 7.6 GB
Docker VM), which is why the windowing exists. All 1M lines ended outside the claim window (run date = real today, the file is
dated 2026), so the eligible count of this run is not a correctness figure. Machine: shared Docker VM, other agents active.

## H. Known CI notes

CI `e2e` failed on runner infra (Ubuntu mirror DNS while installing Playwright deps); rerun requested. `test_timing`
flakiness is unrelated to this branch.
