# M1 S10 reporting (backend core) - notes

Branch `s10-reporting` (stacked on `s9-recovery` 7fe11bb). Migration `20261119_0900_summary_monthly` (down `20261118_0900_recovery_cases`; the revision id must fit varchar(32)). No money logic of S7/S8/S9 was changed; S10 only reads.

## Figure definitions
Owner decisions 2026-11-13 (D-N5/D-N6/D-N7) replaced the "owner to confirm" proposals; the definitions are in BUSINESS_LOGIC §12.2 and implemented once per figure (`core/figures.py`, one SELECT per metric shared by the summary, the drill-down rows and the total). Summary of the changes against the first S10 draft: `claimed` now includes adjustments at the carrying batch's submission month; `collected` includes allocations to adjustments; one at-risk predicate (`recovery.reporting_reads.AT_RISK_WHERE`, reclaimable types only) is used by the dashboard and `GET /cases/summary`; new `recovered_after_write_off`; month truncation of DATE columns is explicit (`::timestamp`). Extra columns vs DATABASE_SCHEMA: `id`, `expiring`, `as_of_date`, `recovered_after_write_off`. New trace route `/trace/claim_adjustment/{id}` (drill-down rows of claimed adjustments).

## Refresh (S10.1)
`refresh_summaries` is tenant-scoped, set-based (a few GROUP BY queries; one delete+insert transaction, idempotent), so not chunked: one attempt is far inside the lease. Triggers: every remittance finalisation (hook, same transaction), `POST /dashboard/refresh`, and a self-scheduling hourly chain (dedupe per hour; dead letter reschedules). Not triggered by claim submission or case status changes: figures lag by at most one hour, or a manual refresh. Calculation runs do not affect any figure. Limitation: the manual/finalise dedupe key is per minute and forever-unique (core.jobs), so a refresh requested in a minute that already had one is coalesced and may miss data changed after that first job ran; the hourly chain heals it.

## Dashboard / drill-down / trace / audit pack (S10.2)
- Trace is four routes (`/trace/claim_line|remittance_line|variance_case|calculation_result/{id}`) instead of one `/trace/{kind}/{id}`, so the isolation route suite covers each id. Same URLs as the spec.
- A rebate-accrual claim line traces to its accrual entry; no single source file row exists (it aggregates purchases). Gap, by design.
- Audit pack: spec is thin ("trace export per claim batch: invoice line -> agreement version -> claim -> payment"); built as CSV/XLSX, one row per claim line and included adjustment, columns: batch, claim ref, line type, invoice/line/date/SKU, source file/row, agreement and version, quantity, unit and claim amount, confirmed paid, remittance documents. No PDF: XLSX/CSV in Release 1, PDF in Release 1.1 (owner decision 2026-11-13).
- Tier tracker: already delivered by S5 (`GET /rebate-tracker`: period volume vs tiers, next tier, amount to go, accrued vs earned); not duplicated. "YTD" = the agreement's measurement period.
- Permissions: `report.read` (all), new `report.refresh` (A P N), new `audit_pack.export` (A P V, per API spec).
- Cross-module reads: to avoid cycles each of claims, reconciliation, recovery exposes `figures` and trace readers (`reporting_reads.py`); like `claims/recon_reads.py` they join other modules' tables in SQL (branch via claim line -> sales line -> document, file rows via import batches). Known deviation from "services not tables" at the SQL level; module imports respect import-linter.
- `claims.render_table` added (generic CSV/XLSX with the existing formula-injection escaping).

## Tests (S10.4)
`tests/integration/test_reporting.py` (ground-truth story CB-VOLT-2026-06 + CM-7781: claimed 10,672.50, collected 7,470.00, short-paid 2,940.00, at-risk 0.00 / on 09-20 2,940.00 and expiring 2,280.00; `test_reporting_money_rule.py`: recovered after write-off, business-month bucket; drill-down sums across pages; trace to source rows; audit pack; roles), `test_reporting_property.py` (Hypothesis on generated cases in a rolled-back transaction), isolation additions in `tests/isolation/*` (new table in all D-series; route tests). Not tested: Playwright W14 (UI is another agent); hourly chain firing over real time; PDF.
