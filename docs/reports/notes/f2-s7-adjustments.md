# F2 notes: S7.4 adjustments and frozen-claims wiring (branch `s7-adjustments`, from `s7-claims`)

Deviations, ADR text and doc deltas for the orchestrator. Only `DATABASE_SCHEMA.md` §3.8 was edited in place (as the
brief asked); everything else below is not applied to the main docs.

## What was built

| Step | Content |
|---|---|
| 0 | Reviewed and fixed the unverified WIP: migration `20261116_0900_adjustments_wiring` (renamed, the first id was 38 characters and exceeded `alembic_version.version_num`), `down_revision = 20261115_0920_claim_lines`. |
| 1 | `claims.frozen.source` is `SqlFrozenClaims`: reads submitted, unreleased `claim_lines` (prior claims, cap consumption net of returns, frozen claims). `NoFrozenClaims` and the test stand-in `FixtureClaims` are gone; tests claim and submit real batches (`calc_support.claim_and_submit`). `ingestion/claims_hook.claimed_line_ids` and the missed-claims `UNCLAIMED` predicate follow the version chain. |
| 2 | `claims/adjustments.py`: proposals (`return_reversal`, `agreement_change`, `rebate_clawback`), list, approve/reject (four-eyes), inclusion in and release from batches; `include_adjustment_ids` and `accrual_entry_ids` on `POST /claim-batches`; rebate-accrual claim lines; batch export carries rebate lines and adjustments. |
| 3 | `source_changed` through the S3 hook, visible on the sales line (`GET /sales-lines/{id}`) and as an `agreement_change` proposal (the new version is compared with the claim on the old one). |

Migration `20261116_0900_adjustments_wiring` (revises `20261115_0920_claim_lines`): `claim_lines.calculation_result_id`
nullable + `ck_claim_lines_result_source`; `claim_adjustments.rebate_accrual_entry_id` (composite FK), composite FK
`claim_line_id -> claim_lines`, CHECKs (`ck_claim_adjustments_clawback|included|approval|claim_line|return_negative`),
unique indexes (`uq_claim_adjustments_clawback`, proposal index gains `calculation_result_id`), the adjustment
transition trigger (inclusion only into a draft batch of the same manufacturer, release only by voiding the batch) and
the batch tie-out trigger (lines + signed included adjustments = `total_amount`, `line_count` counts lines only).

## Interfaces

- `GET /claim-adjustments` (filters `status`, `manufacturer_id`, `adjustment_type`, `claim_line_id`,
  `included_in_batch_id`; keyset, newest first; read `data.read`), `POST /claim-adjustments/{id}/approve|reject`
  (permission `adjustment.approve`, A P; four-eyes on the proposer, 403 `four_eyes_violation`, audited).
  Approve only from `proposed` (409 `invalid_state` otherwise); reject from `proposed|approved`, not once `included`.
- `POST /calculation-runs/{id}/propose-adjustments` now also proposes return reversals and clawbacks; response
  `AdjustmentOut` has more fields (`rebate_accrual_entry_id`, `included_in_batch_id`, approvers, timestamps).
- `POST /claim-batches`: exactly one of `result_ids`, `filter`, `accrual_entry_ids` (new); `include_adjustment_ids`
  (new, default empty). 422 details: `not_found`, `wrong_manufacturer`, `not_approved`, `not_claimable` (accrual delta
  <= 0); 409 `adjustment_already_included` (details carry `batch_id`), 409 `already_claimed` for accrual entries.
  `LineOut.calculation_result_id` is nullable.
- Audit events: `claim_adjustment.proposed|approved|rejected|four_eyes_violation|included|released`; `claim_batch.created`
  carries `included_adjustment_ids`, `adjustments_total`.
- Export: `batch_rows` returns the sales claims, then rebate claims, then the batch's adjustments (signed amount, claim
  reference of the line they correct); new template source `line_type` (`claim | rebate_claim | adjustment:<type>`).
  The default column set is unchanged.
- `agreements.version_rules(db, tenant_id, version_ids)` (additive read, needed for the rounding rule of a prior claim).

## Decisions (please review; none changes a documented money rule)

1. **No double counting of corrections.** A run proposes `agreement_change = (new - frozen) - sum(earlier non-rejected
   agreement_change on that claim line)`; nothing when that is 0. Without this a second recalculation run re-proposes
   the same difference after the first was approved and included. §9.3 gives `new - frozen` for the first proposal; the
   netting only matters from the second run on. A rejected proposal drops out of the sum, so the difference reappears.
2. **Return reversals** are netted along the return line's version chain (review fix): a re-imported return proposes
   only the difference to what earlier non-rejected reversals of any version already cover (CN-5002 4 -> 5 units:
   one extra -12.50, not a second -62.50). The sign of a return correction is free (a corrected return can reduce an
   earlier reversal), so the migration has no sign CHECK for `return_reversal`.
3. **`cannot_be_net_claimed`** (owner decision 1, 2026-11-12, supersedes the earlier reading): a negative return
   reversal of a manufacturer without `allow_negative_claims` stays `proposed`: approve and inclusion are refused with
   409 `cannot_net_claim` (current setting decides, so a withdrawn permission also stops an approved one). A batch whose
   total is <= 0 is refused with 422 `batch_total_not_positive` unless the manufacturer allows negative claims (checked
   at create; no DB CHECK, since the setting is per manufacturer). `testdata` VOLT now has `allow_negative_claims: true`
   (generate.py + `master_manufacturers.json`), which matches ground truth putting ADJ-2026-0001 in the VOLT batch.
4. **Clawback** is proposed for every negative accrual delta (§5.3 R2), claimed or not; the approver decides.
5. **Rebate claim lines**: `accrual_entry_ids` claims a positive delta of one ledger entry as one line (no quantity or
   unit amount); `agreement_version_id` is the entry's. Negative deltas are refused (422 `not_claimable`).
6. **Source change**: "claimed" for the hook means a non-released claim line (draft, approved or submitted batch) on the
   line or any earlier version, so a changed line in a draft batch is flagged too. The new version is treated as
   claimed everywhere (prior claim, frozen claim, missed-claims scan), and gets an adjustment proposal from the
   next run instead of a second claim. Offering it again in `POST /claim-batches` is 409 `already_claimed` exactly
   (`batches._claimed` follows the version chain; review fix, earlier text said 409 or 422). The `source_changed` *variance case* is S9 (`variance_cases` does not exist yet).
7. **Cap consumption** (`SqlFrozenClaims.consumption`) counts every non-released claim line (draft, approved or
   submitted batch; BUSINESS_LOGIC §4.2, review fix), while prior/frozen claims stay submitted-only: claimed quantity (claim UoM) minus returns imported by the run's
   data cutoff (converted with the UoM table), per (agreement, product), claims of the run's own lines excluded. Returns
   are those linked to the claimed version itself.
8. The included adjustment of a *submitted* batch is frozen (trigger); a *draft/approved* batch voided gives it back
   (`included -> approved`, link dropped, audited).

## Deviations from the docs

- `DATABASE_SCHEMA.md` §3.8 updated as ordered (nullable `calculation_result_id`; adjustment columns, CHECKs, trigger).
- API: `include_adjustment_ids` as documented; `accrual_entry_ids` is new; `GET /claim-adjustments` filters are new
  (`API_SPECIFICATION.md` §2.8 lists the endpoint without filters). Doc delta for the orchestrator.
- Outside the owned files: `agreements/calc_reads.py` + `__init__.py` (`version_rules`), `audit/events.py` (actions and
  field allow-list), `ingestion/claims_hook.py` (the S3 hook body, as its docstring prescribed), `exporting.py`
  (`line_type`), `export_jobs.py` (`batch_rows`), `runs.py` (passes the data cutoff to `consumption`), isolation
  fixtures (`tests/isolation/conftest.py`: the S5 proposal is created together with the S7 claim line it now needs).

## Tests (real Postgres; expected values from ground truth or hand derivation in docstrings)

Evidence at the end of this file.

- `test_adjustments.py`: cap case (A 60 submitted, later run excluding A: B gets 40 x 52.00 = 2,080.00, not 3,120.00).
- `test_adjustment_flows.py`: SC-09r return reversal -50.00 (ground truth ADJ-2026-0001), idempotent per run and across
  runs, four-eyes + roles + audit, batch with adjustment ties out (10,410.00 - 50.00 = 10,360.00 = `batch_total`;
  262.50 for the carrying batch), double inclusion 409, wrong manufacturer 422, void releases, DB tie-out refuses an
  untied attach, submitted adjustment frozen, export file ties out; GL-A1 -1,000.00 (and v1a line unchanged, netting,
  reject/renew); GL-R1/R2 rebate claim lines and the -10,400.00 clawback netted to 0.00; `source_changed` from
  `expected_changed_rows.json` (flag, proposal -2,200.00) and a two-version chain that stays eligible and is never offered.
- Existing suites updated: `test_calculation_runs.py` (reference run now has the real batch claimed; the proposal test was
  replaced by the flows above), `test_claims_batches.py` (no stand-in), isolation fixtures, `test_imports.py`.

## Open items / not done

- Frontend adjustments queue (S7.5), Playwright W8-W9: not part of this brief.
- `source_changed` variance case row (S9).
- Returns are linked to the version of the original they were imported against; a re-imported original does not relink
  its returns (same as before this change).
- `consumption` scans all submitted claims per manufacturer window (comment `ponytail:` in `frozen.py`); no measurement
  at 1M lines.

## Test evidence (test database `rebate_test_f2`, compose one-off container)

- Full backend suite after the last code change: `pytest -q --no-cov tests` = **1794 passed**, 4 deselected (the
  live-LLM tests), 0 failed (8 min 50 s).
- New: `test_adjustments.py` (1) and `test_adjustment_flows.py` (8, ground-truth derived); isolation suite 353 tests
  (adjustment routes, list, DB rows), negative control: "181 RLS-dependent tests detected the breach; all independent
  tests still passed" (`python -m tests.isolation.negative_control`, `MIN_TESTS` raised to 353).
- Mutation check: with the old single-version `UNCLAIMED` predicate the "never offered again" test fails.
- Gates: `ruff check`, `ruff format --check`, `mypy` (247 files), `lint-imports` (13 kept), `bandit` clean; frontend
  `npm run lint` and `npm run typecheck` clean after `gen:api` (client regenerated). `pip-audit`, `npm audit` and
  gitleaks on the full tree were not run (only the pre-commit gitleaks hook on staged changes).
- Not tested: concurrency of simultaneous inclusion of one adjustment into two batches (row locks
  `FOR UPDATE` in `lock_for_inclusion` plus the DB trigger; no threaded test); 1M-line cost of `consumption`.

## Review fixes (second round)

Stale-run protection (409 `invalid_state` unless the run is the latest completed one for each of its lines and the
lines are current versions); netting of return reversals along the version chain; caps count live claim lines;
`_claimed` follows the version chain (409 exactly); `propose` takes the per-tenant `claims:` advisory lock (shared with
batch creation); `BEFORE INSERT` guard `fn_claim_adjustment_insert` (amended into the unmerged migration); reasons state
due / already corrected / proposed; owner-SQL test that an included adjustment cannot be re-bound (the S5 trigger
already refused it; the test pins it); API_SPECIFICATION §2.8, DATABASE_SCHEMA §3.8 and BUSINESS_LOGIC §9.3 (netting
rule, marked orchestrator decision pending owner confirmation) updated. `cannot_be_net_claimed` behaviour unchanged.

## Owner decision 1 (2026-11-12) - verification

Verified 7631328 against the fix list: stale-run propose, return-reversal netting on the version chain, caps count all
non-released claim lines, 409 `already_claimed` through the chain, claims advisory lock in propose, insert guard
trigger, re-bind test, API 2.8 and 9.3 docs: all present with tests, no gap found. WIP ede2b4d corrected: its DB CHECK
`total_amount > 0` contradicted "unless allow_negative_claims" and was removed; the rebate test was restored (batch 2 =
10,400.00 - 10,400.00 = 0.00 allowed for VOLT) and now first shows the refusal with the setting off (hand: 0.00 <= 0).
Tests: `test_a_manufacturer_without_negative_claims_blocks_the_return_reversal` (-50.00 stays proposed, approve and
inclusion 409), the zero-batch refusal inside `test_rebate_accrual_claims_and_the_r2_clawback`, engine golden
SC-09r (VOLT -50.00 unflagged; setting off flags). No ground-truth vs BUSINESS_LOGIC money discrepancy found.

## Second financial review fixes

- B1: `frozen._rows` (prior and frozen claims) is back on `_ACTIVE` (frozen, not released); only `consumption` uses `_LIVE`.
  Test `test_a_draft_batch_is_not_a_frozen_claim` (draft 250.00 + return of 4 -> 200.00, no reversal, 200.00 after void).
- S1: migration `20261117_0900_neg_claims_guard` (revises `20261116_0900_adjustments_wiring`; new head): the batch tie-out
  trigger refuses a non-void batch with total <= 0 unless `allow_negative_claims`; the adjustment trigger refuses a
  negative `return_reversal` moving to approved/included without the setting, and inclusion of an adjustment whose claim
  line is not frozen or is released. Test `test_the_database_enforces_negative_claims` (owner SQL; positive control).
  The frozen-line guard has no direct DB test (a frozen/released line state cannot be reached by owner SQL without
  breaking other triggers).
- M1: `approve` and `submit` re-check the setting. M2: `frozen.py` docstrings. M3: the proposal reason says "when proposed"
  (it records the state at proposal; the live setting is enforced at approve and inclusion). M4: JSON line endings restored.
