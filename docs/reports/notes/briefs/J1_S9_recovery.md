# J1 — S9.1–S9.4 + S9.6 backend: recovery queue, deadlines, notifications (branch `s9-recovery`, test db `rebate_test_j1`)
Base: `s8-reconciliation` (I1 is building S8 on it in parallel; it stacks on S7). Read COMMON.md, then ONLY:
CLAUDE.md, PRODUCT_REQUIREMENTS.md §1.9, BUSINESS_LOGIC.md §10.3–10.4, §12, §12.1, DATABASE_SCHEMA.md §3.10,
API_SPECIFICATION.md (recovery/cases/notifications sections + error envelope, pagination, idempotency, If-Match),
SECURITY.md (four-eyes, attachments/uploads), the `core/clock.py` + `core/jobs.py` patterns, `rebate_matching`
`variance.py` / `dispute.py` (case types and dispute assessment come from there; do not re-implement them).

PHASE A — start now (no dependency on I1): pure domain code in `backend/src/rebate/modules/recovery/domain/`
(no DB, no clock, no I/O; `mypy --strict`; add an import-linter contract keeping it pure). Commit + push after each:
1. Deadlines (§12): claim deadline (invoice/ship date, month_end, period_end rebates-only, agreement_end rejected),
   dispute/re-claim deadline, `deadline_unknown`, pure calendar days, no weekend rolling; "today" = date in the
   tenant's time zone from an injected UTC instant + IANA zone (zoneinfo).
2. Case state machine (§12.1): allowed transitions, approver-only write-off, four-eyes (approver ≠ the user who
   proposed the write-off), partial recovery keeps `submitted`.
3. Notification schedule: 14/7/3 days before deadline, dedupe key per (case, kind, offset), digest grouping.
Tests: D1, D2, D3, D4 golden exactly as written in §12 (hand values); leap year; DST spring/autumn and
just-before/after-midnight in the tenant zone vs UTC (injected instants); every allowed and every refused
transition; four-eyes; dedupe; Hypothesis: deadline monotonic in window days, never rolls past the formula date.

PHASE B — when I1 has pushed its S8.3 migrations (`git fetch && git log origin/s8-reconciliation`; merge it in;
until then keep doing Phase A tests): S9.1 migrations `variance_cases`, `variance_case_events`, `case_attachments`,
`notifications` (chain after I1's newest head; tenant rules, RLS ENABLE+FORCE, composite FKs, event rows
append-only by trigger; extend schema-lint + isolation). S9.2 case service: creation from remittance finalisation
(call I1's reconciliation service/hook — agree the interface by reading I1's notes file
`docs/reports/notes/i1-s8-reconciliation.md` on its branch; if it does not exist yet, define a small service function
on your side and document it), owner assignment, notes, attachments through the `files` module (scan/quarantine
rules), reclaim = adjustment proposal through the `claims` service, write-off with four-eyes, `unpaid` detection,
dispute assessment from frozen results. Every transition: `variance_case_events` row + audit in the same transaction.
S9.4 jobs: `deadline_sweep`, notification creation (dedupe), daily email digest (through the existing mail
abstraction; if none exists, STOP and report — do not add a provider). S9 API (backend only; UI goes to Antigravity
later): queue sorted by deadline with `deadline_unknown` first, case detail/timeline, transitions (If-Match),
notifications list/read; permissions on every route; cross-tenant 404; OpenAPI + API spec updated, client regenerated.
Own only: `backend/src/rebate/modules/recovery/**`, its migrations, `backend/tests/**/recovery*`, schema-lint /
isolation registrations, recovery sections of OpenAPI/API spec, generated client. If a rule is ambiguous or docs
disagree: STOP that part and report. Done = full suite + isolation + negctl green, mypy/ruff/import-linter clean, notes in
`docs/reports/notes/j1-s9-recovery.md` (built, test counts, deviations, interface for the UI brief), pushed. Do not merge.
