# S11.3 ASVS L2 review pass — Veyra Recover, `main` @ 9443516

Reviewer: independent, read-only (`.claude/agents/reviewer.md`). Scope: code on `main` only (S7–S10 not merged).
No files changed, nothing committed or pushed. No network scanning tools run.

## 0. Tests run

One-off api container, `test database rebate_test_rev6`, `-e REBATE_AI_WRITER_PASSWORD`:

    pytest -q --no-cov tests/security tests/api tests/isolation/test_routes.py
    → 697 passed in 107.74s

Not run: full suite, `tests/extraction`, `tests/db`, lint gates (bandit/pip-audit/gitleaks), frontend. Those results come from CI and I did not verify them.

Separate check (container, no DB): `str(sqlalchemy.exc.IntegrityError(...))` prints `[SQL: …] [parameters: {'email': …, 'h': '$argon2id$…'}]`. This confirms finding 1.

---

## 1. Findings (most severe first)

### 1. serious — SQL bound parameters (PII, hashes, amounts) reach the logs on any unhandled DB error
- **Where:** `backend/src/rebate/core/db.py:34` (`create_engine(url, pool_pre_ping=True)`, no `hide_parameters`); `backend/src/rebate/api/app.py:166-167` (`log.exception("unhandled_error")`); `backend/src/rebate/core/logging.py:19` (`format_exc_info` renders the full traceback text).
- **Problem:** A SQLAlchemy `StatementError` puts the SQL and *all bound parameters* in its message, and psycopg adds `DETAIL: Key (email)=(…)`. Any unhandled DB exception on the API path is therefore written to stdout logs in JSON. Examples: a unique-violation race in verify-email, confirm-email or upload, where `files/service.py:131` re-raises a non-race `IntegrityError`, or a DataError. The log then holds emails, password hashes, MFA ciphertext and money values. This contradicts SECURITY.md §5 ("never request bodies, passwords, tokens … amounts") and CLAUDE.md rule 12. Worker logs are safe because `core/jobs.py` logs `_safe_error` and the type name only. The API path is not.
- **Evidence:** container check above. `test_secrets_never_reach_the_logs` (`tests/security/test_audit_logs_config.py:170`) never triggers a 500, so it cannot catch this.
- **Fix:** Pass `create_engine(url, pool_pre_ping=True, hide_parameters=True)` in `make_engine` (one line, covers API, worker and AI engine). In the middleware, log only `error=type(exc).__name__` plus the request id, or scrub `exc_info` for `DBAPIError`. Add a test that forces a 500 from a DB error and asserts that no parameter value appears in the captured logs.

### 2. serious — parsers run without the memory/CPU limits SECURITY.md §4 requires (spreadsheet DoS)
- **Where:** `infra/docker/compose.prod.yml` (worker service: no `mem_limit`/`cpus`/`pids_limit`/`deploy.resources`); `backend/src/rebate/modules/ingestion/readers.py:111` (openpyxl in-process); `backend/src/rebate/modules/files/validation.py:130-132` (`MAX_UNCOMPRESSED = 300 MB`, `MAX_RATIO = 100`).
- **Problem:** §4 says "Parsers run in workers with CPU/memory/time limits". Only OCR has rlimits (`extraction/ocr.py:30-32`), and only extraction has a time cap (`documents.py`). Any analyst can upload a 50 MB XLSX that inflates to 300 MB of `sharedStrings.xml`, which passes the 100× ratio check. openpyxl loads shared strings fully into memory, which is several GB of Python objects, inside the shared worker with no container limit. That can take down the worker, or the host.
- **Evidence:** grep of `compose.prod.yml` finds no resource keys. No test covers `archive_too_large` or an under-ratio large workbook.
- **Fix:** Set container `mem_limit`/`cpus`/`pids_limit` on the worker, or run ingestion/extraction parsing in a child process with `RLIMIT_AS`/`RLIMIT_CPU` as in `ocr.py`. Lower `MAX_UNCOMPRESSED`, or cap the `sharedStrings.xml` size and row/cell counts. Add a test for `archive_too_large`.

### 3. serious — header checks are missing entirely (§12 minimum item)
- **Where:** `backend/src/rebate/api/app.py:184-192` (API headers); `infra/caddy/Caddyfile:5-13` (SPA headers: HSTS, CSP, XFO, Permissions-Policy).
- **Problem:** No test asserts `X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`, `Referrer-Policy` on API responses, or anything in the Caddyfile. The only header assertion is `Cache-Control: no-store` (`tests/security/test_auth_flows.py:47`). A regression in `_security_headers` or the Caddyfile would pass CI. Also unverified: whether the SPA works under `default-src 'self'` with no `style-src`. Radix and react-remove-scroll inject `<style>` elements, which this CSP blocks. That is a functional risk which could push someone to weaken the CSP later.
- **Fix:** Add an API test that checks the four headers on a 200, a 4xx and a 500. Add a static Caddyfile assertion, or a `caddy adapt` + JSON check in CI. Run Playwright e2e against the built SPA behind the real Caddyfile and fail on CSP violations in the console.

### 4. minor — role × route matrix derives its expectations from the code under test
- **Where:** `backend/tests/api/test_route_access.py:127-133` (`allowed = role in PERMISSIONS[permission]`).
- **Problem:** The matrix only proves that routes obey `tenancy/policies.py`. A wrong registry entry, or a route that declares a weaker permission than API_SPECIFICATION.md requires, passes. Only three registry facts are cross-checked independently (`test_audit_logs_config.py:253`). This is CLAUDE.md §14 / reviewer rule 3.
- **Fix:** Add a literal table transcribed from SECURITY.md §3 and the per-route permission column of API_SPECIFICATION.md, keyed by `operation_id`. Assert that the registry and each route's declared permission equal that table.

### 5. minor — password change: "revoke all other sessions" is not tested
- **Where:** `backend/src/rebate/modules/identity/service.py:388` (implemented); `tests/security/test_auth_flows.py:207-216` only asserts 200.
- **Problem:** TESTING_STRATEGY §9 requires "revocation on … password change". Deleting line 388 would leave every test green.
- **Fix:** Open a second session, change the password in the first, then assert the second gets 401 and the first still works.

### 6. minor — formula-injection export path untested
- **Where:** `backend/src/rebate/modules/ingestion/router.py:188-193` (`_cell`); `tests/integration/test_imports.py:188-190` checks the content type and codes only.
- **Problem:** The only CSV export on main (`GET /imports/{id}/errors?format=csv`) has protection but no test proves it. Removing the `'` prefix passes CI.
- **Fix:** Import a row whose offending value is `=cmd|' /C calc'!A0` (and `+`, `-`, `@`, tab, CR). Assert that the exported cell starts with `'`.

### 7. minor — upload-abuse corpus incomplete vs TESTING_STRATEGY §9
- **Present:** EICAR, `.xlsm`, `.xls`, `.exe`, renamed exe→csv, csv→xlsx, MIME mismatch, ratio zip bomb, macro parts (VBA, macrosheet, OLE, ActiveX path, external links), encrypted zip entry, entry-count bomb, duplicate entries, oversized (413 by CL and by stream), chunked/unsized, HTML, empty, filename traversal/CRLF.
- **Missing:** XML entity bomb (billion laughs / external entity in a sheet or `sharedStrings`), polyglot file (for example PDF+ZIP), `archive_too_large` path, encrypted PDF at upload (it exists only at extraction level, `tests/extraction/test_documents.py`).
- **Fix:** Add these four cases to `test_files.py` / `test_s2_review.py`.

### 8. minor — ESLint security plugin and Dependabot/Renovate absent
- **Where:** `frontend/eslint.config.js` (no `eslint-plugin-security`); `.github/` has no `dependabot.yml`/`renovate.json`.
- **Problem:** Both are listed in SECURITY.md §6 / §12 ("static analysis … ESLint security plugin"; "Dependabot/Renovate").
- **Fix:** Add `.github/dependabot.yml` for pip/uv, npm, docker and github-actions. That is config only, with no new runtime dependency. The ESLint plugin is a new dev dependency: decide alone per §18.1 and record it.

### 9. minor — per-session row lock serialises all requests of one session
- **Where:** `backend/src/rebate/modules/identity/sessions.py:79` (`SELECT … FOR UPDATE` on every request, held for the whole request transaction).
- **Problem:** One slow request, such as suggest-mapping (up to 2×15 s AI) or an upload streaming to S3, blocks every other request of that user. This is an availability weakness, not a leak.
- **Fix:** Lock only when `touch` actually writes. Or touch in a separate short transaction with `SKIP LOCKED` / `NOWAIT`.

### 10. minor — no dedicated per-IP login limit across accounts
- **Where:** `identity/service.py:188-190` (key = IP+email, 5/min, matching API spec §1); the only per-IP bound across emails is the general 120/min (`api/deps.py:313`).
- **Problem:** Password spraying from one IP: 120 attempts/min, about 170k/day. Unknown-email lockout bounds each address, not the spray. SECURITY.md §1 says "per-account and per-IP rate limiting".
- **Fix:** Add `login:ip:{ip}` at about 20/min. This is a default value, so decide alone.

### 11. nit — SECURITY.md §4 "S2 deviation" note is stale
- `infra/caddy/Caddyfile:38-44` now fronts SeaweedFS with `X-Content-Type-Options nosniff` and `CSP: default-src 'none'; sandbox`. Update the doc, which still says nosniff cannot be added.

### 12. nit — edge rate limiting
- SECURITY.md §6 "edge (Caddy) coarse limits" is not configured, because Caddy core has no rate limiter. Either record that as a deviation, or treat Cloudflare rate rules as the edge layer (OD-3/OD-4).

**Verdict:** 0 blocker, 3 serious, 7 minor, 2 nit.

---

## 2. ASVS 4.0.3 L2 chapter table (code on main)

| Ch | Requirement group | Status | Evidence (code → test) |
|---|---|---|---|
| V1 Architecture | 1.1 SDL/threat model | partial | SECURITY.md §11 risk list, ADRs; no formal per-feature threat model |
| | 1.2 auth architecture, least-priv DB roles | met | `core/db.py:71-111` `assert_safe_role` → `tests/db/test_db_core.py`, `tests/isolation/test_db_rls.py` |
| | 1.4 access-control architecture (single enforcement point) | met | `core/access.py`, router-level `RequestContextDep` (`api/app.py:272`) → `tests/api/test_route_access.py` (meta + matrix) |
| | 1.5/1.12 input & upload architecture | met | `core/validation.py` STRICT; quarantine→scan→promote (`files/jobs.py`) |
| | 1.14 config/segregation | met | `compose.prod.yml` cap_drop ALL, read_only, no-new-privileges, non-root image (`backend/Dockerfile:33-37`) |
| V2 Authentication | 2.1 password security (len 12–256, breach list, no composition, NFKC) | met | `identity/passwords.py`, `core/security.py:37` → `test_auth_flows.py::test_weak_and_common_passwords_rejected` |
| | 2.2 anti-automation, lockout, no enumeration | met (minor 10) | `identity/lockout.py`, `ratelimit.py` → `test_lockout_csrf_enumeration.py` (all), `test_timing.py` |
| | 2.4 credential storage (Argon2id) | met | `core/security.py:47-73` → `test_login_rehashes_when_argon2_parameters_change` |
| | 2.5 recovery (reset tokens 256-bit, hashed, single-use, 30 min) | met | `service.py:404-438` → `test_password_reset_single_use…`, `test_reset_token_expires_after_thirty_minutes` |
| | 2.7/2.8 OTP / TOTP, replay, recovery codes | met | `identity/mfa.py` → `test_mfa_login_totp_replay_and_recovery_code_single_use`, `test_mfa_code_lockout_revokes_pending_session` |
| | 2.3 / 2.6 / 2.9 / 2.10 | N/A | no authenticators issued by email/SMS, no crypto devices, no service accounts |
| V3 Session | 3.1 no tokens in URL | met | cookies only; `api/app.py:176` logs route template |
| | 3.2 new token on login/MFA/switch (fixation) | met | `sessions.create`, `router.py:103` → `test_pre_login_cookie_is_never_accepted_after_login`, `test_session_rotates_on_mfa…`, `test_switch_tenant_rotates_session…` |
| | 3.3 logout/idle/absolute/revocation | met (minor 5) | `sessions.py:77-124` → `test_logout_revokes…`, `test_idle_and_absolute_timeouts`, `test_absolute_timeout_despite_activity`, `test_deactivation_ends_sessions…`, reset/email-change tests; password-change revocation untested |
| | 3.4 cookie flags (`__Host-`, Secure, HttpOnly, SameSite) | met | `sessions.py:136-149` → `test_session_cookie_flags_and_no_token_in_body` |
| | 3.5 token-based sessions | N/A | opaque server-side sessions only |
| | 3.7 re-auth for sensitive ops | met | `access.py:256` → `test_step_up_*`, `test_admin_promotion_requires_step_up`, `test_mfa_setup_and_enable_need_a_recent_step_up` |
| V4 Access control | 4.1 server-side, deny by default | met (minor 4) | `core/access.py` + meta-test → `test_every_route_declares_exactly_one_access_rule`, `test_unauthenticated_gets_401_everywhere` |
| | 4.2 IDOR / tenant isolation | met | RLS FORCE + explicit filters → `tests/isolation/*` (r1–r11, pool context, DB RLS, negative control) |
| | 4.2.2 CSRF | met | `api/deps.py:316-338`, `app.py:73-95` → `test_csrf_and_origin_are_enforced`, `test_csrf_token_from_another_session_is_rejected`, `test_public_posts_need_the_pre_session_csrf_token`, `test_upload_needs_a_session_and_csrf…` |
| | 4.3 admin MFA, step-up | met | workspace `mfa_required` default on → `test_register_verify_login_and_forced_mfa_enrolment` |
| V5 Validation/encoding | 5.1 input validation, mass assignment | met | `STRICT` extra=forbid → `tests/api/test_validation.py`, isolation r6 |
| | 5.2 sanitisation (filenames, header names) | met | `files/validation.py:154`, `validation.py:29` → `test_filenames_are_sanitised` |
| | 5.3 output encoding / formula injection | partial (minor 6) | JSON only; `_cell` in ingestion router, untested |
| | 5.3.4 SQL injection | met | ORM/bound params throughout; one f-string `SET LOCAL lock_timeout = {int}` (`gateway.py:375`) is an int; ruff S rules + bandit in CI |
| | 5.5 deserialization / XXE | partial (minor 7) | defusedxml installed (openpyxl uses it); no entity-bomb test |
| V6 Cryptography | 6.2 algorithms (AES-256-GCM, Argon2id, HMAC-SHA256) | met | `core/security.py` → `tests/unit/test_core.py` (unverified in this run) |
| | 6.3 randomness | met | `secrets.token_urlsafe(32)`, `os.urandom(12)` nonces |
| | 6.4 key management / rotation | met | `ENCRYPTION_KEYS` kid list, `infra/runbooks/secret-rotation.md` |
| V7 Errors/logging | 7.1 no sensitive data in logs | **partial (serious 1)** | `test_secrets_never_reach_the_logs` covers happy paths only; unhandled DB errors leak params |
| | 7.2 security events logged | met | audit events + `AUTHZ_DENIED` → `test_permission_denials_are_audited_and_rate_limited`, `test_five_failures_lock_the_account_and_audit_survives` |
| | 7.3 log protection (append-only audit) | met | `make_append_only` (migration 20261109_2140) → `tests/db`; hash-chain seal = S11.1 (not built) |
| | 7.4 generic errors | met | `app.py:166-170,216-220` |
| V8 Data protection | 8.1 no-store on API, server-side caches | met | `app.py:190` → `test_auth_flows.py:47` |
| | 8.2 client-side (no localStorage) | met | ESLint `no-restricted-globals` (`frontend/eslint.config.js:15`) |
| | 8.3 sensitive data minimisation, PII in logs | partial | see finding 1 |
| V9 Communications | 9.1 TLS, HSTS | partial | Caddy auto-TLS + HSTS (`Caddyfile:5-7`); no TLS config check (needs a host) |
| | 9.2 server comms (SMTP STARTTLS, https S3, DB sslmode) | partial | `config.py:165-182` enforces SMTP/S3 https in hosted; DB `sslmode=require` not enforced (same-host assumption) |
| V10 Malicious code | 10.3 deployed-app integrity, dependency hygiene | partial (minor 8) | pinned digests (Dockerfiles, compose.prod), `uv.lock`, pip-audit, npm audit, licence check, gitleaks in CI (`ci.yml:84-160`); no Dependabot/Renovate, no SBOM (Later) |
| V11 Business logic | 11.1 anti-automation, four-eyes, limits | met | AI per-tenant RPM/caps (`deps.py:351`, `gateway.py`), uploads 30/min, four-eyes helper → `test_uploads_are_rate_limited_per_user`, `tests/extraction/test_gateway.py` caps tests |
| V12 Files | 12.1 size, zip bomb | partial (serious 2, minor 7) | `app.py:98-117`, `validation.py:258` → `test_oversized_files_are_rejected`, ratio bomb; no resource limits on parse |
| | 12.2 type integrity | met | ext+declared+magic → `test_disallowed_and_mismatched_files_are_rejected` |
| | 12.3 execution / path | met | names never in keys (`storage.py:58-63`), sanitised display name |
| | 12.4 storage outside webroot, AV | met | S3 quarantine + ClamAV (`clamd.conf` AlertExceedsMax/Encrypted/OLE2Macros) → EICAR test, `test_content_clamd_cannot_fully_scan_is_never_clean` |
| | 12.5 download (attachment, content-type, nosniff) | met | `storage.py:108-120`, Caddy S3 host headers → `test_presigned_url_downloads_then_expires` |
| | 12.6 SSRF | N/A | no user-supplied URL fetch |
| V13 API | 13.1/13.2 REST auth, methods, CSRF, content types | met | per-route access + CSRF on unsafe methods; FastAPI JSON only |
| | 13.4 GraphQL | N/A | |
| V14 Configuration | 14.1 build/deploy hardening | met | non-root, read-only, cap_drop, digests |
| | 14.2 dependencies | partial | see V10 |
| | 14.3 no debug/docs in prod | met | `app.py:255-262` docs disabled in production |
| | 14.4 security headers | **partial (serious 3)** | set in code/Caddy, never tested |
| | 14.5 request validation (Origin, body limits, Host) | met | `deps.py:316`, `app.py:126-154` → `tests/api/test_body_limit.py` |

---

## 3. SECURITY.md §12 minimum list — status on main

| Item | Status | Where |
|---|---|---|
| Static analysis (bandit, ruff S, ESLint security) | partial | bandit + ruff `S` in `ci.yml:84`/`pyproject.toml:60`; **ESLint security plugin missing** |
| Dependency audit | exists | `ci.yml:85-88` pip-audit `--strict`, `ci.yml:125-126` npm audit, licence check `ci.yml:89-94,127-128` (CI results not re-verified here) |
| Secret scan | exists | gitleaks full history `ci.yml:157-160`, pre-commit `.pre-commit-config.yaml:12-14`, `Makefile:54` |
| Authorization matrix, every route × role | exists (minor 4) | `tests/api/test_route_access.py::test_role_route_matrix`, `::test_unauthenticated_gets_401_everywhere`, `::test_registry_covers_every_openapi_path`; expectations come from the registry, not the doc |
| Isolation suite | exists | `tests/isolation/test_routes.py` (r1–r11, `test_r11_every_parameterised_route_is_covered`), `test_db_rls.py`, `test_pool_context.py`, `negative_control.py` |
| Upload: EICAR | exists | `tests/security/test_files.py::test_eicar_is_infected_blocked_and_admins_are_told` |
| Upload: renamed files | exists | `test_files.py::test_disallowed_and_mismatched_files_are_rejected` [invoice.csv=EXE, invoice.xlsx=CSV, renamed.xlsx macros] |
| Upload: zip bomb | partial | ratio bomb `[bomb.xlsx]` + entry count/duplicates (`test_s2_review.py::test_xlsx_active_content…[too-many-entries]`, `::test_duplicate_zip_entries_are_rejected`); **`archive_too_large` and XML entity bomb missing** |
| Upload: macro XLSX | exists | `test_files.py` [book.xlsm, renamed.xlsx]; `test_s2_review.py::test_xlsx_active_content_and_structure_are_rejected` [vba, macro-content-type, macrosheet, ole-embedding, external-link] |
| Upload: oversized | exists | `test_files.py::test_oversized_files_are_rejected`, `test_s2_review.py::test_chunked_and_unsized_uploads_are_refused`, `tests/api/test_body_limit.py` |
| Session: fixation | exists | `test_auth_flows.py::test_pre_login_cookie_is_never_accepted_after_login`, `::test_session_rotates_on_mfa_and_the_pending_session_dies`, `test_admin_flows.py::test_switch_tenant_rotates_session…` |
| Session: expiry | exists | `test_auth_flows.py::test_idle_and_absolute_timeouts`, `::test_absolute_timeout_despite_activity`, `::test_pre_mfa_session_expires_after_ten_minutes`, `::test_step_up_window_expires` |
| Session: revocation | partial (minor 5) | logout, reset, email change, deactivation, MFA lockout tested; **password change "other sessions" untested** |
| CSRF | exists | `test_lockout_csrf_enumeration.py::test_csrf_and_origin_are_enforced`, `::test_csrf_token_from_another_session_is_rejected`, `::test_public_posts_need_the_pre_session_csrf_token`; `test_s2_review.py::test_upload_needs_a_session_and_csrf_before_the_body_is_read` |
| Header checks | **missing** (serious 3) | only `Cache-Control` asserted (`test_auth_flows.py:47`) |
| DAST (ZAP baseline on staging) | **missing — owner** | needs a staging environment (OD-3/OD-4) |
| External penetration test | **missing — owner** | spending + vendor selection; must happen before significant customer data |

TESTING_STRATEGY §9 extras not on main or missing: XSS payload rendering test (frontend uses no `dangerouslySetInnerHTML`, grep clean, but nothing tests it); header-injection test; SQLi payload sweep (relies on ORM + bandit); open-port check (needs a host).

---

## 4. Needs the owner (cannot be done by the team alone)

1. **ZAP baseline (DAST):** needs a reachable staging environment. That is blocked on hosting decisions **OD-3/OD-4**. The TLS config check, open-port check and Caddyfile-in-situ header check have the same dependency, as does confirming the SPA works under the CSP.
2. **External penetration test:** spending, plus vendor choice and scheduling (CLAUDE.md §18.2). S11.3 only requires it to be *scheduled*. Recommend booking it after S7–S10 merge and staging exists, so the test covers the release surface.
3. Recommendation for later: once staging exists, run ZAP baseline as a CI job against it (TESTING_STRATEGY §9 "on staging in CI").

Everything else above (findings 1–12) is within the team's delegated authority (§18.1) and needs no owner decision. Finding 8's ESLint plugin is a new dev dependency: record the decision and justification.

## 5. Items checked
api middleware (`app.py`, `deps.py`), `core/access.py`, `core/clientip.py`, `core/security.py`, `core/db.py`, `core/config.py`, `core/logging.py`, `core/validation.py`, identity (`router.py`, `service.py`, `sessions.py`, `principal.py`, `lockout.py`, `ratelimit.py`), files (`router.py`, `service.py`, `validation.py`, `scanner.py`, `jobs.py`, `storage.py`), ingestion `service.py` scan gating + `router.py` CSV export, extraction gateway (structure, role, logging), `tenancy/policies.py`, `infra/caddy/Caddyfile`, `compose.prod.yml`, Dockerfiles, `clamd.conf` alerts, `ci.yml` security steps, `.gitignore`/tracked env files, frontend grep for unsafe sinks/localStorage, and the test files listed in §3.
Not reviewed in depth: `mfa.py` internals, `extraction/injection.py` and `verification.py` logic, migrations' RLS SQL (covered by the isolation suite, which passed), and agreements/claims/masterdata business routes beyond the access matrix.
