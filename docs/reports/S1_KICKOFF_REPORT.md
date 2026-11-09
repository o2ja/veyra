# S1 Kickoff Report — Identity, tenancy, RBAC, audit

> Status: **PROPOSED — awaiting owner approval.** No S1 code is written until approval.
> Date: 2026-11-09. Stage size: L. Depends on S0 (approved 2026-11-09).
> Sources read: `PRODUCT_REQUIREMENTS.md` §1.1–1.2 and §3 (US-01..03), `SECURITY.md` §1–§3, §6, §7, §11, `DATABASE_SCHEMA.md` §1–§3.2, §7, §10, `API_SPECIFICATION.md` §1, §2.1, §2.9, §5, `ARCHITECTURE.md` §11 (W1, W2), `TESTING_STRATEGY.md` §5–§9, ADR-006/007/026, current code in `backend/src/rebate/core` and `api/`.

## 0. Pre-S1 check: SeaweedFS doc updates

All applied (commit `159c50f`) and pushed:

| Doc | State |
|---|---|
| ADR-017 | SeaweedFS for local/CI, alternatives, owner conditions for S2, sources |
| `TECH_STACK.md` | §1 object storage row and §6 local Compose row name SeaweedFS (from S2, profile `s2`) |
| `DEPLOYMENT.md` | Service table row `seaweedfs` (S2, profile `s2`, pinned by digest); S3 env vars "SeaweedFS locally" |
| `PROJECT_STATUS.md` | K-6 closed (resolved by ADR-017); R-1 dev/prod parity risk; change-log entry |
| `IMPLEMENTATION_PLAN.md` | S2.2 / S2.4 carry the storage test conditions |

`git grep -i minio` finds only the historical mentions (ADR-017 rationale, K-6, the DEPLOYMENT "replaces MinIO" note). Nothing is missing.

---

## 1. Objectives and scope

Secure multi-tenant foundation: FR-ID-01..08, FR-AU-01..03, NFR-03. Acceptance (IMPLEMENTATION_PLAN S1): two workspaces cannot see each other's data, proved by tests that fail without RLS; MFA works end to end; an admin invites an analyst and a viewer; role limits are enforced on the server; the audit log shows every action.

**Out of scope (not built in S1):** job queue, worker, durable email (S2); files/storage (S2); any business data tables (S3+); audit hash-chain sealing (FR-AU-04, S11); SSO/SCIM, support grants (Later); admin MFA reset for another user (see D-6); a "create a second workspace while logged in" flow (not in the API spec). `idempotency_keys` gets its table and helper with tests because S1 owns the table, but **no S1 endpoint requires an `Idempotency-Key`** (the spec's list starts in S3). It is not wired into any route.

## 2. Tasks (commit-sized, in order)

| # | Step | Plan ref |
|---|---|---|
| 1 | Migration: `tenants`, `tenant_settings`, `memberships`, `invitations` + RLS via `enable_tenant_rls`; global `users`, `sessions`, `user_tokens`, `mfa_recovery_codes`, `rate_limit_counters` (D-4); grants; `fn_user_memberships` | S1.1 |
| 2 | Migration: `audit_log` (+ `_audit.py` helper, immutability trigger, INSERT/SELECT-only grant), `idempotency_keys` | S1.1 |
| 3 | Schema-lint + isolation DB-level tests for the above (§6.2) and the negative control (§6.4) | S1.8 |
| 4 | `core/security`: Argon2id hasher, token generation/hashing, AES-GCM envelope for MFA secrets, breached-password check | S1.2 |
| 5 | Session middleware, CSRF, Origin check, security headers, request tenant context in `api/deps.py` | S1.2, S1.5 |
| 6 | Policy layer: permission registry from `SECURITY.md` §3, `require(permission)` dependency, public-route list, route-declaration meta-test, four-eyes helper (tested in isolation; first real use in S4) | S1.3 |
| 7 | `audit` module: writer (same session/transaction), event catalogue enum, `GET /audit-log` | S1.4 |
| 8 | `identity`: register, verify email, login, lockout, logout, me, password forgot/reset/change, re-authenticate | S1.2 |
| 9 | `identity`: TOTP setup/enable/verify, recovery codes, MFA enforcement | S1.2 |
| 10 | `tenancy`: tenants list/switch, settings GET/PATCH, users list/PATCH (role, status, last-admin protection), invitations create/list/revoke/accept | S1.3 |
| 11 | Rate limiting (Postgres counters) on public auth routes + general per-user limit | S1.5 |
| 12 | Temporary in-process email sender to Mailpit (§5) | S1.6 |
| 13 | Email change (if D-5 = yes) | S1.2 |
| 14 | Frontend: add approved libs (TanStack Query, React Hook Form, Zod, Radix, Playwright); auth screens, MFA, switcher, Users/Invitations/Settings/Audit pages, permission-aware nav, session-expiry handling | S1.7 |
| 15 | Playwright W1–W2; manual browser verification (§8) | S1.8 |
| 16 | Docs, status, checklist, completion report | common |

## 3. Files and modules

**Backend**
- `core/config.py` (new settings, §4), `core/db.py` (tenant + user context), `core/errors.py` (new codes), `core/security.py` (hashing, tokens, encryption, breach check), `core/mail.py` (temporary sender, §5), `core/ratelimit.py`.
- `api/deps.py` (current session, current membership, `require(...)`), `api/middleware.py` (new; the request-id middleware moves here from `api/app.py`; adds security headers, CSRF/Origin, session refresh), `api/routes.py` (registry, public list).
- `modules/identity/` — models, schemas, repository, service, router, `passwords.py`, `mfa.py`, `sessions.py`, `emails.py` (message bodies).
- `modules/tenancy/` — models, schemas, repository, service, router, `policies.py` (permission registry, four-eyes helper).
- `modules/audit/` — models, schemas, repository, service (writer), router, `events.py` (catalogue).
- `migrations/versions/2026111x_*` (two revisions, step 1–2), `migrations/_audit.py`.
- `src/rebate/data/common-passwords.txt` (D-3).
- `tests/isolation/`, `tests/security/`, `tests/api/` (role × route matrix), `tests/db/` (RLS, triggers, grants).

**Frontend**: `features/auth/*` (register, login, verify, MFA setup, MFA challenge, recovery codes, forgot/reset), `features/admin/*` (users, invitations, settings, audit log), `app/` (session provider, switcher, guarded routes, nav), `e2e/` (Playwright).

**Infra/CI**: Mailpit service container in CI (integration tests read its API); Playwright job; `.env.example` gains the new variables (placeholders only).

## 4. Configuration values and proposed defaults

All values in `core/config.py` (pydantic-settings), overridable by environment, validated at startup. Defaults follow `SECURITY.md` §1 and `API_SPECIFICATION.md` §1 where those set them; **bold** = my proposal where the docs are silent.

### Passwords
| Setting | Default | Note |
|---|---|---|
| `PASSWORD_MIN_LENGTH` | 12 | SECURITY §1 |
| `PASSWORD_MAX_LENGTH` | **256** | SECURITY requires max ≥ 128; the cap bounds hashing cost |
| Composition rules | none | SECURITY §1 ("no composition rules theatre") |
| Normalisation | **Unicode NFKC before hashing** | Same password typed on different keyboards verifies |
| Breached-password check | **local list, D-3** | Rejects with `password_too_common` |
| Other rejects | **password equal to the email address** | Only extra rule |
| `ARGON2_TIME_COST` | **3** | RFC 9106 second recommended profile / argon2-cffi default |
| `ARGON2_MEMORY_KIB` | **65536** (64 MiB) | |
| `ARGON2_PARALLELISM` | **4** | |
| Hash length / salt | **32 B / 16 B** | |
| Rehash on login | **yes, when parameters change** | Lets us retune to ≈ 250 ms on prod hardware (SECURITY §1) without forcing resets. A `make bench-argon2` target reports timing; tuning is done in S11 once hosting is chosen (OD-3/4) |

### Login protection
| Setting | Default | Note |
|---|---|---|
| `LOCKOUT_THRESHOLD` | 5 consecutive failures | SECURITY §1 |
| `LOCKOUT_BASE_MINUTES` | 15 | SECURITY §1, response `423 account_locked` |
| `LOCKOUT_PROGRESSIVE` | **doubles per further lockout (15 → 30 → 60 …), cap 240 min; counter resets on successful login** | "progressive" is not quantified in the docs |
| Unknown emails | **counted the same way, keyed by HMAC of the normalised email** | Otherwise only real accounts would ever return 423, which leaks existence (§7 R-3) |
| MFA failures | **count towards the same counter; pre-MFA session dies after 5 wrong codes** | |
| Timing | **always one Argon2 verify per login** (dummy hash for unknown emails); email sends never on the response path (§5) | No-enumeration requirement |

### Rate limits (HTTP 429 + `Retry-After`)
| Scope | Default | Source |
|---|---|---|
| `POST /auth/login` | 5/min per IP+account | API spec §1 |
| `POST /auth/mfa/verify` | **10/min per session** | |
| `POST /auth/register` | **5/hour per IP** | |
| `POST /auth/password/forgot` | **5/hour per IP and 3/hour per email** | |
| `POST /auth/password/reset`, `/auth/verify-email`, `/invitations/{token}/accept` | **10/hour per IP** | |
| General authenticated | 600/min per user | API spec §1 |
| General unauthenticated | **120/min per IP** | |
| `TRUSTED_PROXY_IPS` | **empty** (use the socket peer) | `X-Forwarded-For` honoured only from listed proxies (Caddy) |

### Sessions and CSRF
| Setting | Default | Note |
|---|---|---|
| Token | 32 random bytes; DB stores SHA-256 | SECURITY §1 |
| Cookie | `__Host-session`; `HttpOnly; Secure; SameSite=Lax; Path=/` | Same on localhost: Chrome and Firefox treat `http://localhost` as secure, so `Secure` works in dev. Not verified on Safari |
| `SESSION_IDLE_MINUTES` | 30 | SECURITY §1 |
| `SESSION_ABSOLUTE_HOURS` | 12 | SECURITY §1 |
| `SESSION_TOUCH_SECONDS` | **60** | `last_seen_at`/idle expiry written at most once a minute per session, not on every request |
| `PENDING_MFA_SESSION_MINUTES` | **10** | Session after password, before TOTP; only MFA endpoints work |
| `STEP_UP_WINDOW_MINUTES` | **5** | Password/MFA change, promotion to admin, email change need a re-auth this recent |
| Rotation | login, MFA success, role change, tenant switch | SECURITY §1 |
| Revocation | logout (this session); password change/reset (all sessions); membership deactivation (sessions active in that tenant; every request also re-checks membership); MFA re-enrolment (all others) | SECURITY §1, FR-ID-05 |
| Concurrent sessions | **unlimited** | No requirement; can add a cap later |
| CSRF cookie | **`__Host-csrf`, readable by JS, `Secure; SameSite=Strict`** | Double-submit via `X-CSRF-Token` |
| CSRF value | **HMAC-SHA256(`SECRET_KEY`, session id) once logged in; random pre-session token from `GET /auth/csrf` for public POSTs (prevents login CSRF)** | A token bound to the session, so a planted cookie is useless |
| Origin check | **`Origin` (or `Referer` if absent) must equal `PUBLIC_BASE_URL`'s origin on every mutating request; neither present → 403 `csrf_failed`** | SECURITY §1 |

### Tokens and MFA
| Setting | Default | Note |
|---|---|---|
| Token entropy | 32 bytes, URL-safe, SHA-256 stored, single-use | SECURITY §1 |
| `EMAIL_VERIFY_TOKEN_HOURS` | 24 | SECURITY §1 |
| `PASSWORD_RESET_TOKEN_MINUTES` | 30 | SECURITY §1; issuing a new one voids older ones; a password change voids all |
| `INVITATION_DAYS` | 7 | FR-ID-02 |
| TOTP | **SHA-1, 6 digits, 30 s, ±1 step, issuer "Veyra Recover"** | Authenticator-app compatible; reuse of the same step is rejected |
| Recovery codes | 10 single-use codes, **16 base32 chars (80 bits), SHA-256 stored**, shown once; regenerating voids old ones | High-entropy, so a fast hash is safe; Argon2 ×10 per attempt would be a DoS lever |
| `MFA_ENCRYPTION_KEYS` | **`kid:base64(32-byte key)` list, first = active; AES-256-GCM, ciphertext prefixed with kid** | SECURITY §1/§8 "envelope-ready, key-id prefix for rotation"; needs a new dependency (D-2) |
| Workspace `mfa_required` | true | A user without MFA entering such a workspace gets a session limited to MFA enrolment (`403 mfa_enrollment_required`) |

### Other
| Setting | Default |
|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:5173` locally (links in emails, Origin check) |
| `EMAIL_BACKEND` | `smtp_inprocess` (§5) |
| `SMTP_HOST` / `SMTP_PORT` / `MAIL_FROM` | `mailpit` / `1025` / `no-reply@veyra.local` |
| Audit `authz.denied` rate limit | **1 entry per user × permission per minute** |
| Audit log page size | 50 default, 200 max (API spec §1.2) |

## 5. Email in S1: temporary in-process sender

**What is temporary (S1.6, recorded as known issue K-9 in `PROJECT_STATUS.md`):**
1. `core/mail.py` exposes one function `send_email(message)`. The S1 implementation sends over SMTP with stdlib `smtplib` from a **FastAPI `BackgroundTasks` callback that runs after the transaction commits and after the response is sent**. Nothing is enqueued, nothing is stored.
2. **Not durable:** if the process dies between commit and send, the email is lost. **No retry.** A failure is logged as `email.send_failed` with the template name and request id, never the address or the token.
3. Call sites, all marked `# S2: replace with jobs.enqueue("send_email")`: registration (verify link, or the "someone tried to register with your address" notice), forgot password, invitation, email change (if D-5), verification resend on login of an unverified account.
4. Recovery path for a lost email: the user repeats the action (forgot again, log in again for a fresh verify link); the admin revokes and re-creates an invitation.

**It does not pretend to send real mail:**
- The temporary backend **refuses to start unless `APP_ENV` is `local` or `ci`** (`ConfigError` at startup). In `staging`/`production` the API does not boot until S2 replaces it. So no environment can believe it is delivering to real inboxes.
- It only talks to the configured SMTP host, which is Mailpit locally and in CI. There is no "console/log" fake sender.
- Integration tests assert delivery by reading **Mailpit's HTTP API** (CI gets a Mailpit service container). A test does not pass because a function was called.
- UI and API copy never says "email sent". Public endpoints use the generic "If an account exists, you'll receive an email". The invite UI says "Invitation created".

**Replacement in S2 (S2.1 "Move S1 email sending onto the queue"):** `send_email` becomes a job in the `core/jobs.py` registry, **enqueued in the same transaction** as the change (transactional enqueue), with retry/backoff and a visible dead-letter state. Each call site's `BackgroundTasks` line becomes `jobs.enqueue(...)`. The startup guard is removed and a production SMTP setting is added. One open design point for S2, not decided now: job payloads must not carry secrets (SECURITY §2), but a reset/invite link contains a raw token that is stored only as a hash. S2 must choose between an encrypted token column and a payload exception. I will raise it in the S2 kickoff.

## 6. Tenant-isolation suite v1 (top risk)

### 6.1 Principles
- Two real tenants **A** and **B**, each with an admin, an analyst and a viewer, plus a user **M** who is a member of both. Fixtures come from the S1 tables and grow with every later tenant table.
- Every attack runs against the real Postgres as `rebate_app`, the role the API uses. No mocks.
- **Each layer is proved separately**, because defence in depth hides single-layer failures. Repositories also filter by `tenant_id` (CLAUDE.md §9), so **route tests alone would still pass with RLS removed**. That is why DB-level tests exist, and why a negative control (§6.4) proves they really depend on RLS.

### 6.2 Attacks, expected result, and what happens if RLS is removed

"No RLS" = `ALTER TABLE … DISABLE ROW LEVEL SECURITY` (or `NO FORCE`) on the targeted table.

**DB-level, connected as `rebate_app` (`tests/isolation/test_db_rls.py`)**

| # | Attack | Expected with RLS | If RLS were removed |
|---|---|---|---|
| D1 | `app.tenant_id = A`; `SELECT * FROM memberships/invitations/tenant_settings/audit_log/idempotency_keys/tenants` (no WHERE) | Only A's rows; count equals A's fixture count | B's rows come back; the "no B ids in result" assertion fails |
| D2 | `app.tenant_id = A`; `SELECT … WHERE id = <B's row id>` | 0 rows | 1 row; fails |
| D3 | `app.tenant_id = A`; `UPDATE memberships SET role='admin' WHERE id = <B's>` | 0 rows affected; B's row unchanged (re-read as B) | 1 row updated; fails |
| D4 | `app.tenant_id = A`; `DELETE FROM invitations WHERE id = <B's>` | 0 rows | 1 row deleted; fails |
| D5 | `app.tenant_id = A`; `INSERT INTO memberships (tenant_id = B, …)` | `new row violates row-level security policy` | Insert succeeds; fails |
| D6 | `app.tenant_id = A`; `UPDATE memberships SET tenant_id = B WHERE id = <A's>` (move a row across tenants) | WITH CHECK violation | Row moves to B; fails |
| D7 | No `app.tenant_id` set (and set to `''`) — `SELECT` on every tenant table | 0 rows (fail closed) | All tenants' rows; fails |
| D8 | `app.tenant_id` set to a random UUID | 0 rows | All rows; fails |
| D9 | As `rebate_app`: `ALTER TABLE memberships DISABLE ROW LEVEL SECURITY`, `… NO FORCE …`, `DROP POLICY`, `SET ROLE rebate_owner`, `UPDATE/DELETE audit_log` | `permission denied` / `must be owner` for each | Unaffected by RLS: this test guards the *grants* and ownership that make RLS unbypassable |
| D10 | `SELECT * FROM fn_user_memberships(<M>)` with `app.tenant_id = A` | Exactly M's memberships in A and B (the one sanctioned cross-tenant read), only the columns `tenant_id, tenant name, role, status` | Unaffected by RLS (SECURITY DEFINER). Also asserts the function returns nothing for other users' rows and exposes no other columns |
| D11 | Meta-test: every table with `tenant_id` has RLS enabled **and** forced, a policy using the helper's expression, and `UNIQUE (tenant_id, id)` | Pass | Fails and names the table |

**Pooled-connection context leakage (`tests/isolation/test_pool_context.py`; engine with `pool_size=1, max_overflow=0`, so every transaction reuses the same physical connection)**

| # | Attack | Expected | If RLS were removed / if `SET` were used instead of `SET LOCAL` |
|---|---|---|---|
| P1 | Transaction in tenant A commits; the next transaction on the same connection sets no tenant and selects `memberships` | `current_setting('app.tenant_id', true)` is `''`/NULL; 0 rows | No RLS: A's and B's rows visible; fails. Session-level `SET`: setting still `A`, A's rows visible; fails |
| P2 | Transaction in A **raises** midway (rollback); next transaction for B | Setting is B; only B's rows | Same as above |
| P3 | Interleaved A, B, A, B over 200 iterations on one connection, each asserting it sees only its own rows | Pass | Fails on the first iteration with RLS off; on the second with session-level `SET` |
| P4 | Through the API: request as A's admin, then as B's admin, then unauthenticated `/health/ready`, with a pool of size 1 | Each response has only its own tenant's data | Covered by P1–P3 plus route tests |
| P5 | Mutation check of the context code itself: monkeypatch `transaction()` to use `SET app.tenant_id` (session scope) and rerun P1 | P1 **fails** (proves P1 can detect the bug) | — |

**Routes (`tests/isolation/test_routes.py`, logged in as A's admin unless noted)**

| # | Attack | Expected |
|---|---|---|
| R1 | Route enumerator: every route with an `{id}` path parameter, called with B's id for each method | 404 `not_found`, same body as for a random UUID |
| R2 | Every list route (`/users`, `/invitations`, `/audit-log` with each filter) | No row whose id belongs to B |
| R3 | `PATCH /users/{B's member}` role → admin; `DELETE /invitations/{B's}` | 404; B's row unchanged |
| R4 | `GET /audit-log?entity_id=<B's entity>` / `actor_user_id=<B's user>` | Empty list |
| R5 | `POST /auth/switch-tenant {tenant_id: B}` by a user who is not B's member | 404; session unchanged |
| R6 | Any body containing `tenant_id` (`PATCH /settings`, `POST /invitations`) | 422 |
| R7 | M switches A → B: old session id is revoked, new cookie issued; `/users` shows B only; replaying the old cookie → 401 | Pass |
| R8 | M is deactivated in B while holding a B session → next request | 401 `unauthenticated` (session revoked); A session unaffected |
| R9 | M is demoted in B (admin → viewer) → next request to `PATCH /settings` | 403 (role read per request, SECURITY §11.13) |
| R10 | Same `Idempotency-Key` value used in A and B (helper-level test, since no route uses it yet) | Never replays the other tenant's response |
| R11 | Meta-test: fails if a route is not covered by the enumerator or not on the public list | Pass |

Without RLS, R1–R10 would **still pass** because of the application filters; §6.4 shows that this is by design and proves it.

### 6.3 Composite foreign keys
S1 has no FK between two tenant tables: `tenant_settings`, `memberships` and `invitations` reference `tenants` and the global `users`. The cross-tenant composite-FK attack test therefore starts with the first such FK (S3); in S1 the schema lint enforces the rule. I am stating this rather than writing a test that cannot fail.

### 6.4 Negative control: proving the tests depend on RLS (needs D-1)
A separate CI job, `isolation-negative-control`:
1. Creates a throwaway database `rebate_negctl`, migrates it to head, then **as the owner** runs `ALTER TABLE … DISABLE ROW LEVEL SECURITY` on every tenant table.
2. Runs `tests/isolation/test_db_rls.py` + `test_pool_context.py` against it and **requires that every RLS-dependent test (D1–D8, D11, P1–P3) fails**. The job fails if any of them passes, meaning that test isn't actually testing RLS.
3. Runs the route suite against the same database and **requires that it passes**, proving the application layer holds on its own.
4. Drops the database.

The completion report will contain the real output of this job: the pass/fail table showing each test failing without RLS. That is the evidence you asked for, produced by CI rather than by my reasoning above.

## 7. Auth design points, risks and decisions needed

### Decisions needed from the owner
| ID | Question | My recommendation |
|---|---|---|
| **D-1** | Allow the negative-control job (§6.4) to run DDL (`DISABLE ROW LEVEL SECURITY`) outside migrations in a throwaway test database? CLAUDE.md §11 only allows test DDL in a rolled-back scratch schema. | **Yes**; I'll add it to CLAUDE.md §11 as a second named exception |
| **D-2** | New dependency `cryptography` (AES-256-GCM for `mfa_secret_enc`). Not in TECH_STACK. | **Yes**: the standard, audited choice; no hand-rolled crypto |
| **D-3** | Breached-password check: (a) bundled local list (top 100k common passwords from SecLists, MIT licence, ~1 MB, data only), or (b) HIBP k-anonymity API (sends a 5-char hash prefix to a third party; external availability dependency on login/registration) | **(a)** now; (b) can be added later behind config |
| **D-4** | Rate-limit and lockout counters in a new global Postgres table `rate_limit_counters` (key hashed, window, count; expired rows pruned on write). API spec allows "Postgres or in-process". In-process limits multiply by the number of Gunicorn workers | **Postgres table** (new table not in DATABASE_SCHEMA; I'll add it) |
| **D-5** | Email change: the S1 plan lists "email-change protections", but `API_SPECIFICATION.md` has no endpoint. (a) Add `POST /auth/email/change` (step-up; link to the new address; notice to the old one) + `POST /auth/email/confirm`, or (b) no email change in Release 1 S1, defer | **(a)**, small and in the plan |
| **D-6** | A user who loses both the authenticator and all recovery codes is locked out. An admin MFA reset would let one tenant's admin weaken a global account that also belongs to other tenants. | **No in-app reset in S1**; document an operator runbook (verify identity, reset by SQL as owner, audit entry). Revisit with support grants (Later) |
| **D-7** | Where do auth events without a single tenant go (failed logins, lockouts, resets)? `audit_log.tenant_id` is per-tenant | **For a known user: write one entry into each tenant where they have an active membership** (each tenant's admins see attacks on their users). **Unknown-email failures: structured log only, with a keyed hash of the email**, no audit row |
| **D-8** | New dependency `segno` (pure Python, BSD) to render the TOTP QR code as SVG on the server. The alternative is showing only the text secret | **Yes** |

### Spec additions (documented in the same change as the code; flagged here because they extend an approved spec)
- Endpoints: `GET /auth/csrf`; `POST /auth/password/change`; `POST /auth/reauthenticate` (step-up); `POST /auth/mfa/recovery-codes` (regenerate, step-up); D-5's two email endpoints.
- Error codes: `403 email_not_verified` (only after a correct password, so it reveals nothing; resends the link), `403 mfa_enrollment_required`, `401 reauthentication_required`, `422 password_too_common`.
- Schema columns beyond `DATABASE_SCHEMA.md` §3.1: `users.password_changed_at`, `users.mfa_last_used_step` (TOTP replay), `sessions.reauthenticated_at`, `sessions.mfa_failed_count`, `user_tokens.purpose += 'email_change'` + `new_email citext`; table `rate_limit_counters` (D-4). None are destructive.
- Invitation for an email that already has an account: accepting requires being logged in as that user (no password set); a new user sets a password at accept. The admin sees the same response either way (no enumeration via invite).
- Registration with an existing email: the response is identical; the existing owner gets a notice email (verified account) or a fresh verify link (unverified account).

### Risks
| ID | Risk | Mitigation |
|---|---|---|
| R-2 | Tenant context leak through pooled connections or a forgotten `SET LOCAL` | `transaction()` is the only way to open a DB session (lint test: no other `Session(` use); P1–P5 |
| R-3 | Account enumeration through 423 lockouts, timing or email sends | Unknown emails locked the same way; one Argon2 verify per login; emails after the response; timing test asserts median difference < 20% across 50 calls each (statistical, can be flaky in CI: tolerance documented, not loosened silently) |
| R-4 | `fn_user_memberships(user_id)` is callable by `rebate_app` with any user id; SQL injection there would list another user's workspaces | All SQL bound; function returns only ids, names and roles; D10 pins its columns |
| R-5 | `Secure` cookies on `http://localhost` depend on browser behaviour | Manual checks in Chrome and Firefox; Playwright Chromium in CI; Safari not tested |
| R-6 | Stage size: S1 is L and the frontend adds 4 libraries plus ~12 screens | Backend first, each step green before the next; if it grows beyond L, I stop and propose a split (S1a backend, S1b frontend) |
| R-7 | Temporary email not durable (§5) | K-9 in status; S2.1 replaces it; startup guard |

## 8. Test plan

### Automated (all run in CI; full suite and isolation suite green before completion)
- **Auth (`tests/security/`, `tests/api/`):** register/verify/login/logout; wrong password ×5 → 423 and unlock after the window (controlled `core/clock`); progressive doubling; unknown email locks the same way; identical status/body for existing vs unknown emails on register, login, forgot, invite; timing test (R-3); session fixation (pre-login cookie is never accepted after login); idle expiry at 30 min and absolute expiry at 12 h (clock-driven); revocation on logout, password change/reset (all sessions), deactivation; rotation on login/MFA/role change/switch; token single use, expiry and voiding; CSRF missing/wrong/other session's token → 403; bad `Origin` → 403; MFA: wrong code, replayed step, recovery code single use, enrolment-only session, pre-MFA session limited to MFA routes; step-up required for password/MFA/admin promotion; rehash on parameter change; breached password rejected; secrets/tokens never in logs (log-capture test).
- **AuthZ:** role × route matrix generated from the permission registry (each role → 200/403; unauthenticated → 401); meta-test that every route declares a permission or is public; last-admin protection (demote, deactivate); non-admin role change → 403 + rate-limited audit entry; mass assignment (`status`, `role` on self, `tenant_id`) → 422; four-eyes helper unit tests.
- **Isolation:** §6 in full, plus the negative-control job.
- **Audit:** an entry for every catalogued action, written in the same transaction (handler raises after the write → no audit row and no change); `UPDATE/DELETE` rejected by trigger and by grant; search filters; viewer can read, analyst can read, unauthenticated cannot.
- **Email:** each flow delivers to Mailpit (read via its API) with a working link; startup refuses `smtp_inprocess` when `APP_ENV=staging`.
- **DB:** migrations from empty; schema lint; grants (append-only tables have no UPDATE/DELETE for `rebate_app`).
- **Frontend:** Vitest for forms (validation messages, generic errors) and the session-expiry handler; Playwright W1 (register → verify via Mailpit → MFA setup → recovery codes) and W2 (invite analyst + viewer → accept → role-limited nav and 403 on a forbidden action); axe checks on the auth and admin pages.

### Manual browser verification (Chrome, then Firefox; local stack; Mailpit UI at :8025; TOTP from a phone authenticator app)
I will record what I clicked and observed for each step in the completion report, with screenshots where useful.

1. Register workspace "Acme Electric (SYNTHETIC)". The page shows the generic "check your email" message. In devtools, `__Host-session` is HttpOnly/Secure/SameSite=Lax and no token is in `localStorage`.
2. Try to log in before verifying → "verify your email" message; a new link appears in Mailpit.
3. Open the verify link from Mailpit → log in → forced MFA setup: scan the QR with a phone app, enter the code, see the 10 recovery codes once (a refresh does not show them again).
4. Log out → log in → TOTP prompt → dashboard. Repeat with a recovery code; the same code fails the second time.
5. Wrong password ×5 → lockout message; the same message and behaviour for an email that does not exist.
6. Forgot password for a real and a fake email → identical screens; only the real one gets a Mailpit email; the reset link works once; the second use fails; the other open browser session is logged out.
7. As admin: invite an analyst and a viewer; accept both in separate browser profiles (set password + MFA); the expired/revoked invite shows the generic expired page.
8. As viewer: admin items are hidden in the nav; forcing `/admin/users` by URL shows the permission-denied state; a crafted `PATCH /api/v1/settings` from the console gets 403 (and 403 `csrf_failed` without the header).
9. As admin: change the analyst to approver → the analyst's next action reflects it without logging in again; deactivate the viewer → the viewer's open tab is sent to login on the next request.
10. Try to demote/deactivate the last admin → refused with a clear message.
11. Settings: change time zone, MFA required, four-eyes, AI opt-in → saved; each change appears in the Audit log page with before/after; filters by actor, action and date work.
12. Register a second workspace B with another email; invite the workspace-A analyst into B; switch workspaces with the switcher: lists, users, audit and settings change completely; pasting a workspace-A user id into a B URL shows "not found".
13. Leave a tab idle past the idle timeout (temporarily set to 2 min via env for this check) → the next action shows "session expired" and returns to login after re-auth.
14. Inspect response headers in devtools: CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Cache-Control: no-store` on API responses.

## 9. Expected outcome (demonstrable at completion)
Two synthetic workspaces running locally, fully separated, with the isolation suite and its negative control in CI showing which tests fail when RLS is removed; a complete register → verify → MFA → invite → role-limited flow in the browser with email through Mailpit; every auth and admin action visible in the audit log; and docs updated for the spec additions and decisions above.
