# M2 — S10 money rule and figure definitions (owner decisions D-N2/D-N5/D-N6/D-N7, 2026-11-13)

Read `docs/reports/notes/briefs/COMMON.md` first; its rules apply. Branch: `s10-reporting` (worktree `.claude/worktrees/m1-s10`),
stacked on `s9-recovery`. Test DB: `REBATE_TEST_DB=rebate_test_m2`. **Laptop is weak:** run ONLY the tests for code you change
(`pytest -x -q --no-cov <paths>`); never the full suite, never the negative control, never Playwright.
**Order:** start now on the current branch; G2 (S8) and G3 (S9, D-N2) are done on their branches but S9 has not yet merged the final S8. Do NOT merge s9-recovery until the orchestrator tells you; write the expected figures for the merged state (below) and expect those tests to pass only after that merge. Pass -e REBATE_READONLY_PASSWORD -e REBATE_AI_WRITER_PASSWORD to the one-off container.

## The owner's rule (verbatim; it is the spec)
> An adjustment belongs to the batch that carries it. Batch claimed totals, reconciliation (§10.3) and the
> dashboard count it in that batch [...] Dashboard: claimed = batch totals including their adjustments;
> collected = cash on matched lines. ONE at-risk definition everywhere (dashboard and /cases/summary); month buckets
> in the tenant business time zone. XLSX audit pack is enough for Release 1 (PDF → Release 1.1, document it).
> D-N5: closed cases stay immutable; report "recovered after write-off" as a separate S10 figure.

## Do
1. **claimed** (`claims/reporting_reads.py:22-31`): rows = claim lines of submitted batches (`released_at IS NULL`) at
   `claim_amount` **UNION ALL** adjustments with `status = 'included'` at `amount`, joined on `included_in_batch_id` to a
   submitted batch; month = the CARRYING batch's `submitted_at`; branch = the original line's invoice branch (NULL if none);
   `trace_kind` `claim_adjustment` (add a trace route only if the drill-down needs it to resolve). Fix the module
   docstring (lines 4-5). Includes `recovery_reclaim` adjustments: they are batch content too.
2. **collected** (`reconciliation/reporting_reads.py:17-34`): cash on matched lines = Σ confirmed allocations of accepted/
   reconciled remittances, to claim lines **and** adjustments (drop `JOIN claim_lines` / `claim_line_id IS NOT NULL`;
   branch via the line or the adjustment's original line). Unallocated remainders are not collected.
3. **Month buckets in the business time zone.** Every `month` must come from a DATE that is already a business date
   (`submitted_at`, `payment_date`, `document_date`) or from `timestamptz AT TIME ZONE :tz`. Make DATE truncation explicit,
   e.g. `date_trunc('month', b.submitted_at::timestamp)::date` (a bare DATE resolves to the timestamptz overload, i.e. the
   session zone). The S10 review's exact UTC line is not on disk at drafting time: find it, fix it, name it in the report.
4. **One at-risk definition.** Put one SQL predicate in `recovery` (today: `recovery/reporting_reads.py:24-27,49-56` vs
   `recovery/service.py:383-416`) and use it in both the dashboard figures and `GET /cases/summary`:
   live (`open`, `to_reclaim`, `submitted`), case types `short_paid`, `rejected`, `unpaid`, amount `variance − recovered`,
   `deadline_date IS NULL OR deadline_date <= as_of + 14`. (Orchestrator: confirm "reclaimable types only"; see A1.)
5. **recovered_after_write_off** (D-N5), new metric: Σ confirmed allocations to `recovery_reclaim` adjustments of cases
   with `status = 'written_off'` from remittances that have NO `amount_recovered` event for that case (S9's
   `record_recoveries` skips them, `j1-s9 recovery/service.py:299-300`); month = payment date as for collected. New
   migration adding the column to `summary_claims_monthly` (down_revision `20261119_0900_summary_monthly`); dashboard,
   drill-down, API spec and OpenAPI client regenerated.
6. **Docs.** Figure definitions (claimed, collected, short_paid, recovered, written_off, recovered_after_write_off, at_risk,
   expiring) as a short subsection after BUSINESS_LOGIC §12.1, citing "owner decision 2026-11-13 (D-N6)";
   PRODUCT_REQUIREMENTS FR-RP-03: "XLSX/CSV in Release 1; PDF in Release 1.1 (owner decision 2026-11-13)"; same note in
   API_SPECIFICATION's audit-pack row and in `m1-s10-reporting.md` (replace the "owner to confirm" lines).
7. The audit pack already lists adjustments under their carrying batch (`claims/reporting_reads.py:186-187`): keep it;
   assert it in the test (B2's pack has the ADJ row, CB-VOLT's does not).

## Expected figures for `tests/integration/test_reporting.py` (hand-derived; write as comments)
Story = `build_story` + CM-7781 (paid 2026-07-31), refresh on 2026-07-10. ADJ-2026-0001 rides in B2 (INV-10020/1), not in
CB-VOLT. Assumes G2 (CL-4 short_paid 50.00) and G3 (unpaid deadline) are merged in.
- **claimed** = CB-VOLT 2200 + 3120 + 2080 + 250 + 1000 + 1100 + 660 = 10,410.00, + B2 (312.50 − 50.00 = 262.50)
  = **10,672.50**. (10,360.00 = the GT `batch_total`, where ADJ sits inside CB-VOLT; the real flow cannot produce that
  ordering. Under the rule CB-VOLT alone is 10,410.00 here. See A2.)
- **collected** = 2050 + 3120 + 0 + 200 + (1000 + 1100) = **7,470.00** (= 7,545.00 − 75.00 unmatched).
- **short_paid** = 150.00 + 2,080.00 + 50.00 (CL-4, G2) + 660.00 = **2,940.00** (was 2,890.00).
- Check: 10,672.50 − 7,470.00 = 3,202.50 = 2,940.00 + 262.50 (B2, not yet due).
- Deadlines: CM-7781 cases 2026-07-31 + 60 = **2026-09-29** (Aug 31 = +31, Sep 29 = +60); CL-7 unpaid (G3)
  2026-07-05 + 60 = **2026-09-03** (Jul 31 = +26, Aug 31 = +57, Sep 3 = +60).
- On 07-10 (horizon 07-24): **at_risk 0.00, expiring 0.00** (was 660.00: CL-7 no longer has an unknown deadline).
- On 09-20 (horizon 10-04): **at_risk** = 150 + 2080 + 50 + 660 = **2,940.00**; **expiring** (09-20 … 10-04) =
  150 + 2080 + 50 = **2,280.00** (CL-7, 09-03, is overdue: at risk, not expiring).
- `/cases/summary` at_risk must equal the dashboard's on the same date (07-10: count 0, 0.00).
- recovered 0.00, written_off 0.00, recovered_after_write_off 0.00 in the story.
- New test for D-N5 (hand): CL-1 short_paid 150.00 → reclaim → batch submitted → case written off
  (written_off 150.00) → remittance pays 150.00 on the reclaim adjustment → recovered 0.00, written_off 150.00,
  **recovered_after_write_off 150.00**, case unchanged.
- New test for item 3: tenant zone America/Chicago, a case created 2026-07-31 23:30 local (2026-08-01 04:30 UTC) is in July.

## Other open S10 review findings
The first S10 review's minor list was not saved to disk; the independent review of this round will re-check the whole
diff. Known: refresh dedupe per minute can miss changes until the hourly chain (document it, no change).

## Orchestrator rulings (2026-11-13)
- **A1:** at-risk = live reclaimable cases only (`short_paid`, `rejected`, `unpaid`), the definition M1 proposed and the
  owner confirmed ("dashboard definitions as above; one at-risk definition everywhere"). 09-20 → 2,940.00. Use it in
  both the dashboard and `GET /cases/summary`.
- **A2:** no STOP. 10,360.00 is the ground-truth batch total for the same-batch ordering (covered by S7/S8 pure tests).
  The real-flow story here gives CB-VOLT 10,410.00 + B2 262.50 = 10,672.50; assert those.
- **A3:** collected = Σ confirmed allocations (to claim lines and adjustment items) of accepted/reconciled remittances.

## Tests to run
`pytest -x -q --no-cov tests/integration/test_reporting.py tests/integration/test_reporting_property.py tests/isolation -k "summary or reporting or dashboard or trace"`

## If the docs, ground truth and this rule disagree on any number: STOP and report it (HARD STOP). Do not pick.
Commit in small steps, push `s10-reporting`. No PRs, no merges to main, no PROJECT_STATUS / IMPLEMENTATION_PLAN edits.

## Report (≤ 60 lines)
SHA; files; migration id + down_revision; test commands with counts; hand derivation of each figure; A1-A3 as applied;
not done / not tested; lint gates (ruff, format, mypy, lint-imports, frontend typecheck if the client changed).
