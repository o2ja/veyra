# UI brief: S9 recovery queue (branch `ui/s9`, self-contained)

**Goal.** Build the screens where accountants work variance cases: the queue sorted by deadline, case detail with
timeline, notes and attachments, assign, transition, reclaim, write-off (four-eyes), plus reminders and deadlines. Everything
uses the real API. The server owns every amount, deadline, state transition and reminder.

**Base.** Branch `ui/s9` already exists in the worktree `D:/veyra/veyra-ui-s9`: backend branch s9-recovery (S9 reviewed; S8 is re-merged into it later), with `ui/s7` merged in. It is not on main yet; the orchestrator merges the backend and this branch later. Do not merge, rebase or wait for main.

## Pattern to copy
`frontend/src/features/claims/` (confirm dialogs, `Idempotency-Key`, four-eyes gating with `useSession().me.user.id`),
`features/agreements/draft-editor-page.tsx` (`If-Match` / 412 reload banner), `features/calculations/` (generic tree
for untyped objects, filters in the URL), `features/files/files-page.tsx` (`uploadWithProgress(file, "attachment", …)`,
`download-url`). Shared: `components/ui.tsx`, `masterdata/common.tsx`, `lib/api/*`, `lib/session.tsx`, `app/routes.tsx`.

## Allowed files
- **May edit:** `frontend/src/features/recovery/**` (new), `frontend/src/app/routes.tsx` (route lines) and nav entries
  in `app/app-layout.tsx` (Recovery, Notifications), `frontend/e2e/recovery-*.spec.ts` (new),
  `frontend/e2e/support.ts` (add helpers only), `docs/reports/notes/ui-report-s9.md`.
- **Must not touch:** `backend/`, `migrations/`, `infra/`, `testdata/`, `.github/`, other `docs/`, `frontend/src/api/`,
  `package.json`/lockfile, other feature folders.

## Rules (CLAUDE.md §10)
No business calculations: no days-left countdown arithmetic, no variance − recovered, no "recovered" decision, no
deadline computation, no reminder scheduling, no client-side state machine. Show server strings and dates exactly
(dates formatted in US style only). `dispute_assessment` and event `payload` are untyped, so render them as a labelled
tree. Use the generated client only. The server is the authority on permissions and the UI handles 403. Every screen
has loading, empty, error and permission-denied states, keyboard use, labels and a clean axe check. No mocks.

## API (prefix `/api/v1`; `case.work` = admin, approver, analyst; `case.write_off` = admin, approver; reads all)
- `GET /cases` [`case_list`] `status, manufacturer_id, owner_user_id, deadline_before, reason_code, limit, cursor`.
  Server order: `deadline_unknown` first, then `deadline_date`, then id. Never re-sort client-side.
- `GET /cases/summary` [`case_summary`] → `by_status[]`, `by_reason[]` (count, variance/recovered/written_off amounts),
  `at_risk{amount, count, deadline_within_days}`.
- `GET /cases/{id}` [`case_get`] (`CaseOut`: case_type, reason_code, external_reason_code, claimed/paid/variance/
  recovered/written_off amounts, deadline_date, deadline_basis, deadline_unknown, status, owner, revision, write_off_*,
  dispute_assessment). `GET /cases/{id}/events` [`case_event_list`], `GET /cases/{id}/attachments` [`case_attachment_list`].
- Mutations send `If-Match: "<revision>"` (412 → reload banner), and the response ETag/revision replaces the cached case:
  `PATCH /cases/{id}` [`case_update`] `{owner_user_id|null}` · `POST …/transition` [`case_transition`]
  `{to_status, note?}` (409 `invalid_state`) · `POST …/reclaim` [`case_reclaim`] → `{case, adjustment_id}` ·
  `POST …/write-off` [`case_write_off`] `{reason}` + `Idempotency-Key`. With four-eyes on, the first call records a
  proposal (`write_off_proposed_by/at`, event `write_off_proposed`) and a different approver completes it. Same person:
  403 `four_eyes_violation`. 409 `reclaim_included` while the reclaim adjustment sits in a draft/approved batch.
- `POST /cases/{id}/notes` [`case_note_add`] `{note}` (blank → 422). `POST /cases/{id}/attachments` [`case_attachment_add`] `{file_id}`.
- `GET /notifications` [`notification_list`] `unread, limit, cursor`. `POST /notifications/{id}/read` [`notification_read`].
  Reminders (14/7/3 days) come only from the server's daily sweep. There is no create endpoint, so build no reminder form.

## Screens
1. **Queue `/recovery`**: summary strip from `/cases/summary` (by status, by reason, and "At risk within
   {deadline_within_days} days: {count} cases, {amount}", all server values). Filters in the URL (status, manufacturer,
   reason, owner incl. "Mine" = `me.user.id`, deadline before `<input type="date">`). Table columns: deadline (or "Unknown
   deadline" badge, text not colour), basis, type, reason, manufacturer, variance, recovered, status, owner. Cursor pager.
   The whole row is a link to the detail page (keyboard reachable).
2. **Case `/recovery/:caseId`**: header with amounts as given, deadline + basis, status. Dispute assessment tree.
   Timeline (events, newest first, from/to status, note, actor). Notes form. Attachments (upload → add → list →
   download). Actions behind permissions, each in a dialog:
   - **Assign**: "Assign to me" / "Unassign" for `case.work`. An admin (`user.manage`) may pick a member from `GET /users`.
   - **Change status**: select of `to_status` values from the generated enum except `written_off`, plus an optional note.
     The server validates, so show a 409 as-is.
   - **Reclaim** (`case.work`): on success link "View adjustment" → `/claims/adjustments?status=proposed` (S7 queue).
   - **Write off** (`case.write_off`): reason required. If `write_off_proposed_by` is set and equals me, disable
     "Complete write-off" with the reason. If it is someone else, the button reads "Approve write-off". Show
     `reclaim_included` with a link to the batch.
   - Link "Trace" to `/trace/variance_case/:caseId` only once S10 UI is merged (leave it out otherwise, no stub).
3. **Notifications `/notifications`**: unread/all toggle, list (title, body, due date, link to the entity when
   `entity_type === "variance_case"`), "Mark read" per row. Nav entry "Notifications" with no client-side counting.

## Roles and a11y
A viewer reads everything and gets no action buttons. An analyst gets everything except write-off. A 403 shows the
server message. Tables have headers, dialogs trap focus and close on Escape, and badges use text.

## Playwright (`e2e/recovery-*.spec.ts`, real stack)
Setup: reuse the S8 finalise spec steps (CM-7781 → findings → cases: short_paid 150.00, rejected 2080.00,
unmatched_payment 75.00, unpaid 660.00, the last with an unknown deadline).
1. `recovery-queue`: the four cases are listed, the unknown-deadline one first. The filter by reason works. Summary values
   are the server strings. A viewer sees no actions.
2. `recovery-case`: assign to me, add a note (shown in the timeline), attach a file, transition to `to_reclaim`, reclaim
   → adjustment link. A stale revision (second tab) gives 412 and the banner.
3. `recovery-writeoff`: approver A proposes → same user's Complete is disabled → approver B completes →
   status `written_off`, written-off amount shown. An analyst has no Write off button (and gets 403 by API).
4. `recovery-notifications`: list renders, mark read moves it out of Unread, and the axe check passes on every page.
Reminders appear only after the daily sweep. Do not trigger the sweep from the UI. If no reminder exists, assert the
empty state. Selectors use role, label or name only, scoped. Run at most 4 specs per batch. On 429, stop and ask the
owner to clear `rate_limit_counters`. Never loosen an assertion.

## Commands (from `frontend/`)
`npm run lint`, `npm run typecheck`, `npx vitest run src/features/recovery`, `npx playwright test e2e/recovery-*.spec.ts`.
At the end: `npm test`, then full Playwright in batches of ≤ 4.

## Gaps (record, do not work around)
No `allowed_transitions` on `CaseOut` (the UI offers every status and the server refuses). No `days_to_deadline`
(so no countdown). `GET /users` needs `user.manage`, so non-admins can only assign themselves. `/cases/summary` at-risk must
use the same single definition as the dashboard (owner 2026-11-13). The server must align it, and the UI only labels it.
