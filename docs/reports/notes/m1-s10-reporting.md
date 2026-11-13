# M1 S10 reporting (backend core) - notes

Branch `s10-reporting` (stacked on `s9-recovery` 7fe11bb). Migration `20261119_0900_summary_monthly` (down `20261118_0900_recovery_cases`; the revision id must fit varchar(32)). No money logic of S7/S8/S9 was changed; S10 only reads.

## Figure definitions (the docs name the figures but not their definitions; owner to confirm)
All per manufacturer, branch (invoice branch of the claim line, null if none) and month. One SELECT per metric (`core/figures.py`), used for the summary, the drill-down rows and the drill-down total, so they cannot disagree.
- `claimed`: claim lines of submitted, unreleased batches, by batch submission month. Adjustments are NOT included (corrections, not claims).
- `collected`: confirmed allocations to claim lines of accepted/reconciled remittances, by payment date (else document date, else created). Allocations to adjustments (e.g. the -50.00 return reversal) are not collected.
- `short_paid`: variance of cases of type short_paid, rejected, unpaid (money claimed and not paid), by case-open month. overpaid, unmatched_payment, source_changed are excluded (not owed to us).
- `recovered`, `written_off`: the cases' `recovered_amount` / `written_off_amount`, by case-open month.
- `at_risk`: open variance of live reclaimable cases with unknown deadline or deadline <= as-of date + 14 days (overdue included). DIFFERS from `GET /cases/summary` `at_risk`, which also counts non-reclaimable types. Decision for owner: align one way or the other.
- `expiring`: subset of `at_risk` with a known deadline from the as-of date to +14 days.
- Extra columns vs DATABASE_SCHEMA: `id`, `expiring`, `as_of_date`.

## Refresh (S10.1)
`refresh_summaries` is tenant-scoped, set-based (a few GROUP BY queries; one delete+insert transaction, idempotent), so not chunked: one attempt is far inside the lease. Triggers: every remittance finalisation (hook, same transaction), `POST /dashboard/refresh`, and a self-scheduling hourly chain (dedupe per hour; dead letter reschedules). Not triggered by claim submission or case status changes: figures lag by at most one hour, or a manual refresh. Calculation runs do not affect any figure. Limitation: the manual/finalise dedupe key is per minute and forever-unique (core.jobs), so a refresh requested in a minute that already had one is coalesced and may miss data changed after that first job ran; the hourly chain heals it.

## Dashboard / drill-down / trace / audit pack (S10.2)
- Trace is four routes (`/trace/claim_line|remittance_line|variance_case|calculation_result/{id}`) instead of one `/trace/{kind}/{id}`, so the isolation route suite covers each id. Same URLs as the spec.
- A rebate-accrual claim line traces to its accrual entry; no single source file row exists (it aggregates purchases). Gap, by design.
- Audit pack: spec is thin ("trace export per claim batch: invoice line -> agreement version -> claim -> payment"); built as CSV/XLSX, one row per claim line and included adjustment, columns: batch, claim ref, line type, invoice/line/date/SKU, source file/row, agreement and version, quantity, unit and claim amount, confirmed paid, remittance documents. No PDF (FR-RP-03 says PDF/XLSX): PDF not built. Gap.
- Tier tracker: already delivered by S5 (`GET /rebate-tracker`: period volume vs tiers, next tier, amount to go, accrued vs earned); not duplicated. "YTD" = the agreement's measurement period.
- Permissions: `report.read` (all), new `report.refresh` (A P N), new `audit_pack.export` (A P V, per API spec).
- Cross-module reads: to avoid cycles each of claims, reconciliation, recovery exposes `figures` and trace readers (`reporting_reads.py`); like `claims/recon_reads.py` they join other modules' tables in SQL (branch via claim line -> sales line -> document, file rows via import batches). Known deviation from "services not tables" at the SQL level; module imports respect import-linter.
- `claims.render_table` added (generic CSV/XLSX with the existing formula-injection escaping).

## Tests (S10.4)
`tests/integration/test_reporting.py` (ground-truth story CB-VOLT-2026-06 + CM-7781: claimed 10,722.50, collected 7,520.00, short-paid 2,890.00, at-risk 660.00 / later 2,890.00 and expiring 2,230.00; drill-down sums across pages; trace to source rows; audit pack; roles), `test_reporting_property.py` (Hypothesis on generated cases in a rolled-back transaction), isolation additions in `tests/isolation/*` (new table in all D-series; route tests). Not tested: Playwright W14 (UI is another agent); hourly chain firing over real time; PDF.
