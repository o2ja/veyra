# ui/s7-adjustments review 1 (fef83b3) — 2026-11-13 evening

Verdict: NOT MERGEABLE (0 blocker, 6 serious, 7 minor, 2 nit). Production code follows CLAUDE.md §10 (no money maths,
generated types only, correct paths/params, Idempotency-Key, permission hiding + server four-eyes). lint, typecheck,
vitest (6) pass. The report's three API gaps are real.

Serious
1. e2e/claims-adjustments.spec.ts:342-352: scenarios 4 (adjustment_already_included) and 5 (cannot_net_claim) not implemented.
2. spec :241, :266, :293, :296: assertions guarded by `if (await x.isVisible())` (loosened).
3. spec :243: /adjustments proposed/i matches the empty state "No adjustments proposed." only.
4. spec :338-340: batch total check only asserts the label exists.
5. spec :189, 201, 234, 254, 259, 289, 319, 322: CSS/structure selectors; :196, :311 selectOption({index:1}).
6. claim-adjustments-page.tsx:177-199: tablist without arrow-key handler; inactive tabs tabIndex=-1 → unreachable.

Minor
7. missed-claims-page.tsx:210-224: manufacturer change keeps selectedAdjIds/selectedAccrualIds.
8. missed-claims-page.tsx:100-115: accrual query keyed on uncommitted text; no manufacturer filter in the API (gap).
9. common.tsx:53-75: per-entry error drops entry_id/adjustment_id.
10. claim-adjustments-page.tsx:88-99: cannot_net_claim matched on message text + hard-coded fallback.
11. common.tsx:41-50: dead top-level batch_id branch.
12. claim-batch-detail-page.tsx:70-83, :446: carried adjustments limit 100, no pager, client count in title.
13. missed-claims-page.tsx:436-451: mode toggle lacks aria-pressed.

Nit: 14. proposed_by shown as raw UUID (:291). 15. claim reference links to the batch, not the batch line (:238).
