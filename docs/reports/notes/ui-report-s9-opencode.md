# UI report S9 — opencode

Author: **opencode** (the Antigravity-owned prompt was run by this agent; this file is the opencode report).
Branch: `ui/s9`, worktree `D:/veyra/veyra-ui-s9`.
Last code SHA: `6daeaaa`; this report is the tip commit (`git log -1` on `ui/s9`).

## Screens done

- `/recovery` — queue in server order (unknown deadlines first as returned), summary strip (by status,
  by reason, at-risk window + amount), filters in the URL (status, manufacturer, reason, owner=mine,
  deadline before), keyset pager, loading/empty/error/403, row link reachable by keyboard.
- `/recovery/:caseId` — amounts rendered as the server's strings, deadline + basis + unknown flag,
  dispute assessment as a labelled tree, timeline (newest first, payload tree), notes, attachments
  (real upload → scan poll → attach, download-url), assign (`If-Match`), change status, reclaim (+ S7
  adjustments link), write-off with `If-Match` + stable `Idempotency-Key` per revision, four-eyes
  propose/complete labels, `412` reload banner, `reclaim_included` batch link.
- `/notifications` — unread/all list, mark read, case link, loading/empty/error/403.

Routes/nav lines only: `/recovery`, `/recovery/:caseId`, `/notifications`; nav Recovery, Notifications.
All dialogs are Radix (focus trap, Escape), tables have captions/scopes, every control has a label.

## Commands (frontend/)

| Command | Result |
| --- | --- |
| `npm run lint` | pass — eslint 0 warnings, prettier clean |
| `npm run typecheck` | pass — `tsc -b` (one earlier run OOMed locally; passed on retry, code unchanged) |
| `npx vitest run src/features/recovery` | pass — 8 tests, 1 file |
| `npm test` | pass — 42 tests, 7 files |
| `npx playwright test e2e/recovery-*.spec.ts --list` | 4 tests in 4 files (explicit filenames; the glob fails on Windows) |
| Playwright execution | **not run (stack on another branch)** — `http://localhost:8000/openapi.json` → 404, `/api/v1/cases` count 0; Vite `:5173` → 200 |

## Not run / untested

- All four specs: `recovery-queue`, `recovery-case` (incl. 412), `recovery-writeoff` (four-eyes),
  `recovery-notifications` (incl. axe on queue, case, notifications).
- The 403 screens are asserted only by those specs; not exercised by hand.
- Attachment scan failure/timeout branches (cannot make the scanner fail on demand).

## Gaps

1. `GET /api/v1/cases`, `/api/v1/cases/{id}`: no `allowed_transitions`, no `days_to_deadline`.
   Expected: server-approved targets and days remaining. Actual: only the `TransitionIn.to_status`
   enum; the UI lists the six statuses minus `written_off` and relies on the server's refusal.
   Screen: `/recovery/:caseId` (change status).
2. `GET /api/v1/users` needs `user.manage`. Expected: assignable members for `case.work`. Actual:
   `MemberList` for user managers only; analysts can assign "me"/unassigned. Screen: case assign.
3. `AttachmentOut` = `id`, `file_id`, `added_at`, `added_by`. Expected: filename and scan status.
   Actual: UI shows the file id. Screen: case attachments.
4. S7 adjustments queue `/claims/adjustments?status=proposed` has no route in this worktree (S7
   adjustments UI not merged). Expected: the queue. Actual: dangling link target. Screen: reclaim.
5. `reclaim_included`: no documented error fields. Expected: batch id. Actual: the UI links a batch
   only if a detail named `claim_batch_id`/`batch_id` carries a UUID, else to `/claims/batches`.
6. No trace endpoint for cases in this branch's schema, so the S10 trace link is omitted.
