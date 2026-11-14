# ui/s8 review 1 (e2761ac = Antigravity 063e0c4 + final S8) — 2026-11-14

Verdict: NOT MERGEABLE (0 blocker, 5 serious, 7 minor, 1 nit). Report claims were false: `npm run lint` FAILS (prettier:
src/features/reconciliation/logic.test.ts), `npm run typecheck` FAILS (new-remittance-page.tsx:122 `parsed.error.errors`
→ zod v4 `.issues`); vitest 3 pass. "Gaps: None" contradicts the brief (no findings after reload, no claim-line search, D-N11).

Serious
1. Job polling never ends: UI waits for "completed"/"finished"; server statuses are queued|running|succeeded|failed
   (schema.d.ts:8849, core/jobs.py:260). new-remittance-page.tsx:100 never navigates; remittance-detail-page.tsx:180 never
   clears the job id; "failed" polls forever with no error. Fix: stop on succeeded/failed, navigate/invalidate in useEffect,
   show job.error. SAME BUG in claims/claim-batch-detail-page.tsx:215 (ui/s7, ui/s7-adjustments) — fix there too.
2. Server errors not shown: confirm/reject (detail:259/282) handle only 412 (409 invalid_state, line_categorised,
   match_invariant silent); categorise (:308) and run matching (:226) 409s silent → ValidationDetailsAlert per mutation.
3. Findings table (detail:969-994) lacks claimed, paid, external_reason_code and a claim reference (brief §3).
4. "Manual match" offered on categorised lines (detail:790) — server refuses (LineCategorised); hide when category is set.
5. e2e/reconciliation-workbench.spec.ts cannot pass: no submitted VOLT batch setup; selectors that match nothing
   ("Submit remittance" → "Create and ingest remittance", "Finalise reconciliation" → "Finalise", "Document total amount"
   → "Document total", reason-code texts :81-86, "Reconciliation lines" :157); `if (await x.isVisible())` guards
   (:79, :141, :144, :174, :190) and `count() > 0` (:162/:167); unscoped `.first()`; missing scenarios (duplicate_remittance,
   viewer 403, stale tab, applied manual match line 4 → CL-4 200.00 → short_paid 50.00, uncategorised_lines detail,
   categorise ONLY line 6); findings asserted unscoped by text and with GT line 4 (null variance) instead of final S8
   (short_paid 50.00). Stale-tab case yields 409 invalid_state, not 412 (decide() checks status first, matching.py:469).

Minor
6. `res as unknown as {...}` casts (new:144, detail:233): use RemittanceAccepted; untyped reconcile 202 = backend gap.
7. ConfirmDialog/ValidationDetailsAlert/useManufacturerMap copied from claims/common.tsx → import.
8. Filter chips (detail:648) need aria-pressed.
9. "Document total" always required (detail:475) + hint implying a client total rule → use the server problem message.
10. Non-403 load errors all shown as "Remittance not found." (detail:360) → ErrorAlert.
11. Manual match only on unmatched rows (server accepts suggested too); batch lines limit 100, no pager (detail:143).
12. new-remittance-page:115-123 Zod parse inside mutationFn → zodResolver field errors.
Nit 13. per-mount Idempotency-Key is fine (server stores successes only).
