# UI brief — S5/S6 batch 2: review fixes + missing e2e (+ two S4 minors)

Same rules, folders and verify commands as `ui-brief-s5-s6.md` (re-read only its "Folders", "Rules" and "Verify"
sections). Branch `ui/s5-s6` (main already merged in). Independent review verdict on batch 1: FAIL. Fix all of this:

## Serious
1. `extractions/extraction-detail-page.tsx` ~136-145: do NOT call `GET /files/{id}/download-url` on page load (the
   server writes an audit "download" per call; the URL expires in ≤300 s). Fetch it on click (mutation in the link
   handler), then `window.open(url + "#page=N", "_blank", "noopener")`. Show its error (e.g. 409 file not scanned) in an Alert.
2. `extractions/logic.ts` `isFieldFlagged` (~46-50) is client-side confidence logic (forbidden). Remove it. A field is
   visually distinct only when the server's `field.confidence` is `conflict` or `unsupported`; show that label and
   the server's `reasons` as given. Update its unit test.
3. `e2e/calculations.spec.ts`: the exception-filter assertion (`resultsTable.or(...)`) cannot fail. Make the data
   produce at least one known exception (e.g. a sales line whose sell price differs from the agreement →
   `price_mismatch`, or a SKU with no agreement → `no_agreement`), filter by status=exception, assert every row's
   Status cell reads the exception and the exception code column shows the code; open that row's trace, press the
   first expander, assert `aria-expanded="true"` and a child node visible.

## Minor
4. `run-detail-page.tsx` ~367-394: results query must refetch when the polled run status becomes terminal
   (add the status to the queryKey or invalidate on transition).
5. `parseExtractionResult` (logic.ts ~23-29): validate every key the page uses (`fields`, `blocking`,
   `needs_acknowledgement`, `indicators`, `notes.a/b`, `unresolved`) with Array/object guards; if malformed, show an
   error state ("Extraction result could not be read") instead of throwing. Unit test both cases.
6. `draft-preview-panel.tsx` ~16-17: no invented default 200. Send what the user typed (integer) or block submit
   with a field message; let the server's 422 speak.
7. Show `details[]` messages of 422 errors under the alert in the preview panel and the results/accrual filters
   (add a small local component in your feature folder; do not edit `components/ui.tsx`).
8. `extractions-page.tsx` ~38/90: file picker — only `scan_status === "clean"` agreement files are selectable;
   add paging (keyset `cursor`) instead of the first 100 only.
9. `extractions-page.tsx` ~62: do not construct a fake `ApiError`; keep a local validation message in state.
10. `rebates-page.tsx` ~184-201, `run-detail-page.tsx` ~582-598: free-text UUID / exception-code filters apply on
    submit (a small form with an "Apply" button), not per keystroke; accrual filters live in the URL like screen 2.
11. a11y: preview-panel row buttons get `aria-label={`Trace for ${document_number} line ${line_number}`}`; fix
    heading levels (extraction-detail h4→h3 under h2; preview panel h3→h2 under the Card h1).

## e2e coverage still missing (add to `e2e/calculations.spec.ts`)
12. "Propose adjustments" on a completed run: click, assert the proposals table or its empty message renders with
    the server's `compared` count shown.
13. Draft preview panel: open an SPA draft, run the preview, assert "Preview — not saved" and at least one line row.
14. Rebate tracker with a populated row: the e2e data must include an approved rebate agreement (copy the rebate
    steps of `e2e/agreements.spec.ts`) so `/calculations/rebates` shows one tracker row and one ledger row.

## Two S4 minors (agreements folder, `draft-editor-page.tsx` only)
15. When a server payload lacks keys the editor shows defaults for, mark the draft as changed (enable "Save draft")
    and show a one-line notice "Some fields were missing and show defaults; save to store them" so what is shown
    is what gets stored on save. Never auto-save.
16. `focusEl` (~40-45): for a disabled control, focus its own `<label>` (or the cell/row) — never the wrapping
    `<fieldset>`; give the target `tabIndex=-1` only for that focus.

## Local e2e note
The local dev API counts registrations per IP (5/hour) and does not trust the dev proxy's forwarded IP. Run the
specs in batches of at most 4 files and before each batch run (dev database only, nothing else):
`docker exec veyra-recover-postgres-1 psql -U postgres -d rebate -qc "delete from rate_limit_counters"`.

## Orchestrator's own run (2026-11-12)
lint, typecheck, vitest 30/30 pass. Playwright: 7 of 8 specs pass; `e2e/calculations.spec.ts:48` FAILED locally
with `locator.click: Test ended` (test timeout while the shared worker was busy). 17. Make it robust: wait for the
run with `expect.poll`/`toHaveText` on the status cell (up to 3 min), give the test `test.setTimeout(300_000)`, and
never click before the element is visible. It must pass locally twice in a row.
