# S1 Identity, tenancy, RBAC, audit: completion report

## Owner decisions needed
1. **S1 signed off by the owner on 2026-11-10** ("I approve everything"). Nothing open.
2. No other decisions are open. Rule-4 items (product, spending, legal, hosting, destructive, weakened security): none.
3. For later, not now: the `rebate_lookup` role needs `BYPASSRLS`; check that the managed Postgres chosen under OD-3 allows creating it.
4. –
5. –

Date: 2026-11-10. Kickoff: `docs/reports/S1_KICKOFF_REPORT.md` (approved 2026-11-09 with D-1..D-8). Commits `e0b43a3` … HEAD on `main`.

## 1. What was built (vs. plan)

All S1.1–S1.8 tasks are done (`IMPLEMENTATION_PLAN.md`, ticked with notes).

- **Schema (2 + 2 migrations):** global `users`, `sessions`, `user_tokens`, `mfa_recovery_codes`, `pending_registrations`, `rate_limit_counters`; tenant tables `tenants`, `tenant_settings`, `memberships`, `invitations`, `audit_log` (append-only), `idempotency_keys` (table only), all with RLS enabled + forced via the helper. Cross-tenant lookups `fn_user_memberships`, `fn_invitation_by_token` are `SECURITY DEFINER`, owned by the NOLOGIN `BYPASSRLS` role `rebate_lookup`, `search_path` pinned. `rebate_readonly` has no access to credential tables. Migration `20261110_0900` restores `rebate_owner`'s `EXECUTE` on `fn_user_memberships` (found by the runbook dry run, §4); `20261110_1000` adds `users.last_failed_at` (review F5).
- **identity:** registration held as a pending row until the email link is used (no pre-account takeover); Argon2id login with progressive, decaying lockout that behaves identically for unknown emails; TOTP (replay guard: step must be newer than the last) and 10 hashed recovery codes; server-side sessions (only the SHA-256 is stored; rotation on login, MFA, MFA enable and tenant switch; idle 30 min, absolute 12 h; revocation on logout, password change/reset, deactivation, email change, operator MFA reset); step-up (5 min) for password/MFA changes and admin promotion; password reset; email change (password **and** MFA, D-5); invitation acceptance.
- **tenancy:** workspace creation, settings with `If-Match`, memberships (role/status per workspace only, last-admin guard), invitations (create/list/revoke/accept/expire), permission registry from SECURITY §3, four-eyes helper (used from S4).
- **audit:** writer in the caller's transaction with per-entity field allow-lists, event catalogue, `GET /audit-log` (filters, cursor pages) now showing actor **name and email**; denials audited (rate-limited) in a separate transaction.
- **API pipeline:** one transaction per request committed **before** the response; failure bookkeeping (lockout counters, rate limits, failed-login audit rows) commits; Origin + CSRF double-submit (session-bound HMAC; pre-session token for public POSTs); Postgres rate limits; security headers; logs record route templates, never raw paths or tokens.
- **Temporary background pool (K-9):** emails to Mailpit over SMTP from a 2-thread pool fed after commit; the forgot-password lookup and token work also runs there, so known and unknown addresses take the same time on the response path. Refuses to start outside `local`/`ci`. Replaced by jobs in S2.1.
- **Frontend:** register, verify, login, MFA challenge, MFA setup with one-time recovery codes, forgot/reset, accept invite, confirm email, security page; Users, Invitations, Settings, Audit log; workspace switcher; permission-aware navigation and permission-denied state; step-up dialog; "your session has ended" after server-side expiry; tokens read from the URL fragment and removed from the address bar.
- **Ops:** `infra/scripts/ops/mfa-reset.sql` and `promote-admin.sql` with the rewritten runbook `infra/runbooks/locked-out-admin.md` (D-6).

## 2. Test results (fresh runs, 2026-11-10)

| Suite | Result |
|---|---|
| Backend `make test-backend` (real Postgres 16, Mailpit) | **253 passed**, 0 skipped, coverage **94.7 %** |
| Isolation suite (`tests/isolation`) | **59 passed** (+ schema meta-test in `tests/db`) |
| Negative control (RLS disabled in throwaway DB `rebate_negctl`) | **OK**: all 20 RLS-dependent tests fail with an `ISOLATION-BREACH` assertion; all independent tests (grants, lookup shape, route-level R1–R11) still pass |
| Frontend Vitest | 7 passed |
| Playwright (local Chrome; CI Chromium) | W1–W2 passed (with the new verify-with-password step); axe WCAG 2.1 AA on 10 S1 pages: 0 violations (after one fix, §4) |
| Lint gates (`make lint`) | ruff, mypy, import-linter (7 contracts), bandit, pip-audit, ESLint, tsc, npm audit, gitleaks: clean |
| CI on `main` | see §10 |

**Do the tests fail when the code is broken?** Spot-checked by temporarily breaking the code, then restoring it (plus the review fixes F3 and F4: their new tests fail on the old code):
- Resetting the failure counter on a correct password → `test_correct_password_with_wrong_totp_cycles_still_lock` fails.
- Disabling the CSRF check → all 3 CSRF tests fail.
- RLS disabled → the negative control above.
- Session-level `SET` instead of `SET LOCAL` → P5 detects it (automated).

## 3. Isolation evidence

Two workspaces A and B and a user in both:
- **DB attacks as `rebate_app`:** cross-tenant select/update/delete/insert/move, no or unknown tenant, `DISABLE`/`NO FORCE`/`DROP POLICY`/`SET ROLE`/`row_security=off`, owner under FORCE, readonly/ai_writer blocked from the lookups and credential tables.
- **Pooled connection of size 1:** the tenant setting never survives commit or rollback, over 200 interleaved transactions.
- **Every parameterised route** answers 404 for B's ids exactly as for a missing id. Lists and audit filters never show B. A client-sent `tenant_id` gets 422.

The negative control shows which tests depend on RLS and which on the application filters (§2).

## 4. Manual verification (what was actually seen)

**Tools:**
- 2026-11-09: your Chrome via the Claude extension, plus a scripted Playwright run in installed Chrome (steps 1–5 with screenshots).
- 2026-11-10: your Chrome via the extension. The admin acted through the real API in a separate session, so the user's browser shows the effect of another person's action.

| Check | Observed |
|---|---|
| Register → email in Mailpit → verify link | Generic "check your email" screen; link opened; address bar no longer had the token; redirect to sign-in with "email confirmed" |
| Sign-in before verifying | Same message as a wrong password |
| Forced MFA setup | QR rendered; setup key reveal works; 10 recovery codes shown once, not on revisit |
| Recovery code reuse | Second use refused |
| Lockout, real vs unknown email | Identical messages for every attempt (scripted run, screenshots compared) |
| Settings change → audit | "Settings saved"; audit row shows time zone and four-eyes before → after |
| Last admin demote | "A workspace needs at least one active admin"; role stayed admin |
| Headers / storage | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Cache-Control: no-store`; session cookie not readable from JavaScript; the app writes nothing to localStorage (the origin held two keys from another app on port 5173) |
| Invite → accept (new user) → MFA | Viewer lands on home; nav shows Home, Settings, Audit log only |
| Role change by admin (viewer → analyst) | On reload the member's nav lost "Audit log"; `/audit` shows "Not available" |
| Deactivation by admin | Member's open tab sent to sign-in on the next click |
| Forgot / reset | Generic screen; link from Mailpit worked once; reuse refused ("This link is no longer valid"); old password refused, new password led to the MFA prompt (MFA kept) |
| Idle timeout (API restarted with 2 min) | After 2 min 15 s idle the app was on sign-in |
| Absolute timeout (12 h simulated by moving `absolute_expires_at` into the past in the DB, idle still valid) | Click → sign-in with "Your session has ended. Sign in again to continue." |
| Runbook §2 dry run (MFA reset) | Missing variable → stopped; unknown id → "nothing changed", rolled back; first real run stopped with `permission denied for function fn_user_memberships` and **rolled back fully** → fixed by migration; rerun: MFA off, 0 codes, 0 live sessions, audit row with ticket/operator; next sign-in forced re-enrolment in Chrome |
| Runbook §3 dry run (promote) | Non-member refused; member promoted; audit `membership.role_changed_by_operator` analyst → admin |

**Found and fixed during the checks:**
- missing `EXECUTE` grant (runbook);
- no "session ended" message after idle/absolute expiry or deactivation;
- the workspace switcher's accessible name did not contain its visible text (axe);
- audit actor shown only as an id.

## 5. Code review checklist (`CODE_REVIEW_CHECKLIST.md`)

| Section | Result |
|---|---|
| 1. Functionality | Pass. FR-ID-01..08, FR-AU-01..03 implemented and tested; exercised in the browser (§4). Differences from docs are listed in §6 |
| 2. Code quality and architecture | Pass. import-linter green (protected module contracts); routers → services; the audit module gets actor names via a lookup wired by the API layer (no import cycle). New deps approved (D-2, D-8) or standard dev tools (`@testing-library/user-event`, `@axe-core/playwright`) |
| 3. Security | Pass, subject to the reviewer findings in §7. Every route declares access (meta-test); role × route matrix; isolation suite + negative control; no secrets/tokens/emails in logs (log-capture test); parameterised SQL only; CSRF/cookies/rate limits tested; audit in the same transaction. AI: not applicable |
| 4. Database integrity | Pass. Migrations from empty + drift check in CI; constraints and CHECKs; append-only audit (trigger + grants). `EXPLAIN` on realistic volume: **not done**; S1 tables are small, deferred to S11 per `DATABASE_SCHEMA.md` §9 |
| 5. Financial correctness | Not applicable (no money in S1) |
| 6. Error handling and observability | Pass. Envelope and stable codes; request id in logs and errors; UI loading/empty/error/denied states |
| 7. Testing | Pass. Real Postgres; spot-checks by breaking code (§2); CI blocks skipped tests |
| 8. Performance | Partly. Keyset pagination on audit; Argon2 at production parameters measured only in the timing test. Notes: one `rate_limit_counters` upsert per request (general limit); no load test (S11) |
| 9. Frontend | Pass. Generated client; permission-aware UI; axe clean on S1 pages; no tokens in storage |
| 10. Documentation | Pass. SECURITY, API spec, DATABASE_SCHEMA, DEPLOYMENT, TECH_STACK, runbook, PROJECT_STATUS, plan ticks updated in the same changes |

## 6. Deviations from the docs (all recorded in the docs listed)

- `rebate_lookup` owns the `SECURITY DEFINER` functions, because FORCE RLS applies to the owner (`DATABASE_SCHEMA.md` §2).
- Registration creates a pending row only; `email_not_verified` dropped (`API_SPECIFICATION.md` §2.1).
- `POST /invitations/accept` takes the token in the body; email links use the URL fragment (API spec).
- Audit read is admin, approver and viewer, not analyst (SECURITY §3 conflict resolved to the narrower rule).
- New error codes `link_expired` and `precondition_failed`; settings `PATCH` requires `If-Match`.
- `api/middleware.py` not created; the small middleware stays in `api/app.py`.
- The negative control runs as a step of the `backend` CI job, not as a separate job (kickoff §6.4).
- Timing test: 15 samples, 25 % tolerance (kickoff said 50 / 20 %); review F7.
- `POST /auth/verify-email` also requires the registration password (review F8).
- The forgot-password work runs in the temporary background pool (K-9), not in the request.
- "Rotation on role change" (SECURITY §1): roles are read on every request and sessions carry no role, so a role change takes effect on the next request without rotating; deactivation revokes that workspace's sessions.

## 7. Independent review

The reviewer (`.claude/agents/reviewer.md`, read-only, security focus) reviewed the full S1 code diff (`a5fd2df..70d92b7`) and the working tree. It ran as a general-purpose agent loaded with that definition, because the `reviewer` type is not registered when the session starts outside the repo folder. Verdict: **1 blocker, 4 serious, 8 minor, 4 nit.** Of the 17, 13 are fixed (`29f9c21`), 2 are partly fixed or corrected in the docs, 2 are not fixed, and the blocker was not a code defect. Of the kickoff review's §10 responses, it confirmed 18 as implemented, found 2 only partly done (now completed as F4 and F7 below), and did not verify 1 (the runbook, done in §4).

| # | Sev | Finding | Response |
|---|---|---|---|
| 1 | blocker | `deps.py` had CSRF switched off (`MUTATION` line) | **Not a code defect.** The reviewer read the working tree during my deliberate breakage spot-check (§2); the line was restored minutes later, and HEAD never contained it (`git grep MUTATION` empty). Guard added anyway: a test fails if any `MUTATION` marker is in `src/` |
| 2 | serious | A stolen session of a user without MFA can enrol the attacker's authenticator without step-up, and the new session counted as a fresh step-up | **Fixed.** `/auth/mfa/setup` and `/enable` require a recent step-up; enabling MFA no longer grants one. The UI routes this through the step-up dialog. Test `test_mfa_setup_and_enable_need_a_recent_step_up` |
| 3 | serious | A password reset left a pending email-change token valid | **Fixed.** Setting a password voids every open token of the user. Test `test_password_reset_voids_a_pending_email_change` (fails on the old code; checked) |
| 4 | serious | Rate-limit hits were rolled back with failing requests, so bad tokens were never limited | **Fixed.** Hits commit in their own short transaction. Test: 11 bad reset tokens → 10× 410, then 429 (fails on the old code; checked) |
| 5 | serious | A real account's failure counter never expired, while the unknown-email counter expired after 24 h, which reveals that the account exists | **Fixed.** New column `users.last_failed_at`; failures older than the 24 h window are forgotten, the same as for unknown emails. Test: 4 failures, 25 h, 1 failure → both 401 |
| 6 | minor | Three tests checked less than their names claim | **Fixed.** After-commit callbacks are tested through the real pipeline (a failing request does not run them); the log test flushes the pool first; the rotation test shows the pre-MFA cookie is replaced and dead afterwards |
| 7 | minor | Timing test uses 15 samples and 25 % tolerance, not the planned 50 / 20 %; no accept-invite timing test | **Recorded, not changed.** 15 interleaved samples keep the test under 15 s; 25 % catches the 44 % leak the test found in forgot-password. Accept-invite timing: **not tested**. Both branches run one Argon2 operation, but there is no measurement. Listed in §8 |
| 8 | minor | The owner of an address could complete the attacker's registration by clicking the attacker-triggered link | **Fixed.** Verify needs the registration password as well (API and UI). Test `test_victim_cannot_complete_an_attackers_registration` |
| 9 | minor | Docs promise rotation on role change and revocation of all sessions on password change | **Docs corrected** (SECURITY §1): roles are read per request (no rotation needed); a password change keeps the current, just re-authenticated session and revokes all others |
| 10 | minor | The negative control could pass on an empty or short run; D11 is not run in it; it runs as a step, not a separate job | **Fixed:** it now fails if any RLS-dependent prefix matched no test, or if fewer than 59 tests ran. D11 (schema meta-test) runs in the normal suite; it inspects the catalogue, so running it with RLS off proves nothing. "Step not job" recorded as a deviation (§6) |
| 11 | minor | Mail header errors were swallowed; background logs had no request id | **Fixed.** The message is built inside the `try`, failures are logged by class only, and the request id is carried into pool logs. Names reject control characters (no CR/LF in headers) |
| 12 | minor | Garbage `X-Forwarded-For` → all such clients shared one `None` bucket | **Fixed.** Falls back to the proxy peer; test updated |
| 13 | minor | Rehash on login was not tested | **Fixed.** Test: hash made with `t=1`, login under `t=2`, stored hash has `t=2` |
| 14 | nit | Email change consumed the TOTP step before validating the new address | **Fixed.** Validates first |
| 15 | nit | `create_workspace` / `open_invitation` switch tenant context without restoring it; `tenant_id` not bound into request logs | **Not fixed (accepted).** Both switches are deliberate: the rest of the request then works in that new tenant, and nothing reads the old context. Binding `tenant_id` into the request log line: **not done**, listed in §8 |
| 16 | nit | Reactivating a disabled admin bypassed the step-up that promotion needs | **Fixed.** Any change to `status=active` needs step-up; test added |
| 17 | nit | Shutdown did not flush the background pool | **Fixed.** Flushed in the lifespan exit |

## 8. Known issues and not tested

- **K-9 (temporary):** a crash or SMTP failure loses an email or a forgot-password token; the user repeats the action. Replaced by jobs in S2.
- **Not tested:**
  - Safari and Firefox (manual checks in Chrome only; Playwright Chromium in CI);
  - a real authenticator app on a phone (TOTP computed by code, RFC 6238);
  - load/performance;
  - the timing test runs locally and in CI, and is statistical (25 % tolerance).
- **Idle/absolute expiry by hand:** idle was checked by hand with a 2-minute setting. Absolute was simulated by editing the session row in the DB, not by waiting 12 h; the automated tests use a fake clock.
- **Users lost the authenticator and all codes:** only the operator runbook helps (D-6, by design).
- **K-7:** Starlette's `httpx` deprecation warning in tests (unchanged).
- **Accept-invite timing:** not measured (review F7). `tenant_id` is not in the request log line (review F15).
- **Rate limits:** each hit uses an extra pooled connection; fine at this scale, revisit in S11 load tests.

## 9. Readiness for S2

Nothing blocks S2. S2 must:
- replace the K-9 pool (email and forgot-password work) with jobs;
- settle the token-in-job-payload question (kickoff §5);
- extend the isolation suite to files and jobs.

## 10. CI and closure

- CI green on `29f9c21` (all jobs: backend with negative control, frontend, e2e, api-contract, secrets, images). Final closing commit adds `SKIP LOCKED` to the rate-limit prune (no wait on rows held by the request's own transaction); security/API tests 145 passed locally.
- The second reviewer pass (on this report and the fix diff) was **stopped before it finished**, at the owner's instruction to close S1. Its findings were not seen; re-run it at the start of S2 if wanted.
- Owner sign-off: 2026-11-10.
