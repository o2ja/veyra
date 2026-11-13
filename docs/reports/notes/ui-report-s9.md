# UI report S9 — recovery

Author: **opencode**. Branch: `ui/s9` (worktree `D:/veyra/veyra-ui-s9`).
Last code SHA: `e6226bf`; this report is committed on top of it.

## Screens done

- `/recovery` — queue in the server's order (unknown deadlines first), summary tables (status, reason,
  at-risk window/amount), URL filters (status, manufacturer, reason, owner=mine, deadline before), keyset
  pager, loading/empty/error/403, keyboard-reachable full-row link. No client re-sorting.
- `/recovery/:caseId` — server amounts as strings, deadline/basis/unknown flag, dispute assessment and
  event payload trees, timeline (newest first), notes, attachments (real upload → scan poll → attach →
  download URL), assign (`If-Match`), status, reclaim, write-off with `If-Match` and a stable
  `Idempotency-Key` per revision, four-eyes labels, `412` reload banner, `reclaim_included` batch link.
- `/notifications` — unread/all list, mark read, case link, loading/empty/error/403.
- Routes/nav lines only: `/recovery`, `/recovery/:caseId`, `/notifications`; nav Recovery, Notifications.
- Radix dialogs (focus trap, Escape); table captions/scopes; labelled controls; no business calculations.

## Commands (frontend/)

| Command | Result |
| --- | --- |
| `npm run lint` | pass — eslint 0 warnings, prettier clean |
| `npm run typecheck` | pass — `tsc -b` |
| `npx vitest run src/features/recovery` | pass — 8 tests, 1 file |
| `npm test` | pass — 42 tests, 7 files |
| `npx playwright test <4 recovery files> --list` | 4 tests in 4 files (explicit filenames; glob is unreliable on Windows) |
| Playwright execution | **not run — no local stack**: `:8000/openapi.json` and Vite `:5173` unreachable, so `/api/v1/cases` could not be probed |

## E2E fixture (written, not executed)

`bootstrapRecovery` now builds the real scenario through the API: VOLT settings (claim window 250 days,
submission-date dispute window 60 days, lag 30, tolerance 0.01), SPA agreement approved by approver A,
calculation run, a claim batch of exactly the seven ground-truth lines approved and submitted by approver
A with `submitted_at=2026-07-05`, CM-7781 extracted/accepted/reconciled (`succeeded` job), the suggested
subset match confirmed, then finalised. Specs target the short-paid case for reclaim/write-off; reclaim is
not preceded by a manual `to_reclaim` transition (reclaim makes that move itself).

## Not run / untested

- All four specs: queue, case (assign/note/attachment/reclaim/412), write-off four-eyes + analyst 403,
  notifications + axe on queue, case and notifications.
- The 403 screens are asserted only by those specs; not exercised by hand.
- Attachment scan failure/timeout branches (the scanner cannot be made to fail on demand).
- The fixture itself is the main risk: it compiles and lists, but its server interactions were not run.

## Gaps

1. `GET /api/v1/cases`, `/cases/{id}`: no `allowed_transitions` and no `days_to_deadline`. The UI lists
   the six statuses minus `written_off` and relies on the server's refusal. Screen: change status.
2. `GET /api/v1/users` needs `user.manage`; analysts can only assign "me"/unassigned. Screen: assign.
3. `AttachmentOut` has `file_id` but no filename/scan status, so the UI shows the id. Screen: attachments.
4. `/claims/adjustments?status=proposed` has no route in this worktree (S7 adjustments UI not merged), so
   the reclaim success link is dangling. Screen: reclaim.
5. `reclaim_included` has no documented error fields; the UI links a batch only when a detail named
   `claim_batch_id`/`batch_id` contains a UUID, else to `/claims/batches`. Screen: reclaim.
6. No case trace endpoint in this branch's schema, so the S10 trace link is omitted.

## Brief discrepancy

The brief expects the **unpaid** case to have the unknown deadline. The real API always counts an unpaid
deadline from the claim submission when a dispute window is configured (owner decision D-N2), so the
unpaid deadline is known. With `dispute_window_basis="submission_date"`, the sole unknown deadline is the
**unmatched payment** (it has no claim line to count from). The fixture and queue spec assert that
observable behaviour; the brief's exact wording cannot be produced through the public API.
