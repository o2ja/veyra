# Security

> Baseline: OWASP ASVS Level 2 as the review reference. Security requirements here are **Release 1 requirements unless marked "Later"**. Related: `ARCHITECTURE.md`, `DATABASE_SCHEMA.md` §2/§7, `API_SPECIFICATION.md`, `AI_PROCESSING.md` §9.

## 1. Authentication

| Control | Requirement |
|---|---|
| Passwords | Argon2id (`argon2-cffi`, parameters tuned to ≈ 250 ms on prod hardware, stored in config); minimum 12 characters; breach-list check against a **bundled local list** (S1 D-3: Xato 10M-password set, released to the public domain by its author M. Burnett 2015, top 1M as redistributed by SecLists (MIT); only entries ≥ 12 chars are kept; no network call); no composition rules theatre; max 256 (≥ 128 required); NFKC-normalised before hashing; rehash on login when parameters change |
| MFA | TOTP (`pyotp`); workspace `mfa_required` default true; 10 single-use recovery codes (hashed); MFA secret encrypted at application level with AES-256-GCM (`cryptography`, S1 D-2), keys from the environment variable `ENCRYPTION_KEYS` (`kid:base64key,…`, first = active; ciphertext carries the kid; rotation: `DEPLOYMENT.md` §2.4); TOTP replay of the same time step rejected. **The otpauth URI, the secret and the QR code are never logged** (S1 D-8) and are returned only by `POST /auth/mfa/setup` with `Cache-Control: no-store`. No in-app MFA reset by admins (S1 D-6); operator runbook `infra/runbooks/locked-out-admin.md` |
| Login protection | Per-account and per-IP rate limiting; progressive lockout (5 failures → 15 min, `423`); uniform timing and messages (no account enumeration on login, reset, register, invite). Unknown emails are counted and locked exactly like real accounts (keyed HMAC of the normalised email). **Raw email addresses of unknown users are never logged** (S1 D-7); logs carry only that keyed hash. Counters live in Postgres (`rate_limit_counters`, S1 D-4) |
| Sessions | Opaque 256-bit random token → cookie `__Host-session` (`HttpOnly; Secure; SameSite=Lax; Path=/`); DB stores only SHA-256 of the token; idle timeout 30 min, absolute 12 h; rotation on login, MFA success, MFA enrolment and tenant switch. Roles are read on every request and sessions carry no role, so a role change applies on the next request without rotation (S1 review finding 9). Immediate revocation on logout; deactivation (that workspace's sessions); password reset, email change and operator MFA reset (all sessions); password change (all **other** sessions; the current one was just re-authenticated) |
| CSRF | Double-submit token (`X-CSRF-Token`) for all mutating requests; `SameSite=Lax` as second layer; `Origin`/`Referer` check |
| Reset/verify/invite tokens | 256-bit random, stored hashed, single-use, expiry (reset 30 min, verify 24 h, invite 7 d) |
| Email changes | Requires **password re-entry and a current MFA code** in the same request (S1 D-5); link to the new address (24 h, single-use); notice to the old address; on confirm all other sessions are revoked |
| Step-up | Re-enter password/MFA for: changing password/MFA, creating API-level exports of all data, user role changes to `admin` |
| SSO/SCIM | Later (R2), OIDC/SAML as add-on |

## 2. Multi-tenant isolation (defence in depth)

1. **Application layer:** session carries active tenant; `core/db.py` opens each request transaction with `SET LOCAL app.tenant_id`; no code path accepts `tenant_id` from clients; repositories never build queries that omit the tenant context.
2. **Database layer:** composite FKs `(tenant_id, id)`; **RLS enabled and forced** on every tenant table; `rebate_app` has no `BYPASSRLS` and does not own tables; fail-closed when the setting is missing.
3. **Workers:** every tenant-scoped job row carries `tenant_id`; the worker sets tenant context per job transaction before touching tenant data; a tenant-scoped job without it fails before its handler runs. Global identity jobs (`tenant_scoped=False`) carry no tenant and touch only global tables. Payloads hold ids only: no tokens, no email addresses (the job mints a token and stores only its hash). The `jobs` table has no RLS (the worker claims across tenants); every API read filters `tenant_id` explicitly and the isolation suite covers it.
4. **Object storage:** keys prefixed `tenants/{tenant_id}/…`; downloads only via presigned URLs generated after a DB policy check; bucket not public.
5. **Everything else:** logs include `tenant_id` but no data; caches (none initially) keyed by tenant; exports and AI calls are tenant-scoped; error reports scrubbed.
6. **Verification:** automated isolation suite (`TESTING_STRATEGY.md` §8) runs on every CI build: two tenants; every route, job, export, file URL attempted across the boundary; RLS tested by using the app role with a mismatched `app.tenant_id`; a meta-test fails CI if a tenant table lacks RLS.

Tenant-leak risks that are not obvious: job payloads/queue tables (Procrastinate tables are global; payloads contain ids only, no financial data), idempotency keys (scoped by tenant), cached OpenAPI examples, presigned URL lifetime/guessing, PDF/Excel exports embedding other data, shared temp directories on workers (per-job temp dir, wiped), logs, backups (contain all tenants; restoring one tenant requires tooling, planned later), support access.

## 3. Authorization and RBAC

Roles: `admin`, `approver`, `analyst`, `viewer`. Permissions are enforced in the policy layer of each module; UI hiding is cosmetic.

| Permission | admin | approver | analyst | viewer |
|---|:-:|:-:|:-:|:-:|
| `user.manage`, `invitation.manage`, `settings.write` | ✔ | | | |
| `settings.read`, all `*.read` (data, reports) | ✔ | ✔ | ✔ | ✔ |
| `audit.read` | ✔ | ✔ | | ✔ |
| `file.upload`, `import.run`, `masterdata.write`, `mapping.write` | ✔ | ✔ | ✔ | |
| `agreement.draft` (create/edit/submit) | ✔ | ✔ | ✔ | |
| `agreement.approve/reject/return/cancel` | ✔ | ✔ | | |
| `calculation.run` | ✔ | ✔ | ✔ | |
| `claim_batch.create/export` | ✔ | ✔ | ✔ | |
| `claim_batch.approve/submit/void` | ✔ | ✔ | | |
| `adjustment.approve` | ✔ | ✔ | | |
| `remittance.upload/reconcile/match` | ✔ | ✔ | ✔ | |
| `case.work` (notes, assign, transition except write-off) | ✔ | ✔ | ✔ | |
| `case.write_off` | ✔ | ✔ | | |
| `ai.use` | ✔ | ✔ | ✔ | |
| `jobs.manage` | ✔ | | | |

**Four-eyes (maker-checker)**, workspace setting `four_eyes_required` (default on): the user who created or last edited a draft, batch, adjustment, or case write-off request cannot approve it. Enforced in services; violation → `403 four_eyes_violation`; audited. Last admin cannot be removed or demoted. Role changes take effect on the next request.

**Platform support access:** none standing. Support staff have no tenant membership. Break-glass access requires a time-limited, customer-visible, audited grant (`support_grants`; Later) and read-only DB role with RLS scoped to that tenant.

## 4. File upload security

| Control | Detail |
|---|---|
| Allow-list | `.csv`, `.xlsx`, `.pdf` only. Reject `.xlsm`, `.xls`, `.xlsb`, archives, executables, scripts, HTML, SVG |
| Triple check | Extension, declared MIME, and magic bytes (`python-magic`) must agree |
| CSV text encoding | UTF-8, UTF-8 with BOM, Windows-1252 and UTF-16 LE/BE **with BOM** are accepted (S3); the detected encoding is recorded on the import batch. Without a BOM, strict UTF-8 is tried first, then Windows-1252. Bytes valid in neither, and text containing NUL (binary content, UTF-16 without BOM) are rejected with 422 `encoding_unsupported` and a "save as CSV UTF-8" message; decoding never substitutes replacement characters. Other single-byte code pages are read as Windows-1252 (a known limit: the preview shows the result before confirmation) |
| Size | CSV/XLSX ≤ 50 MB, PDF ≤ 25 MB (config); streaming upload with running size check; reject early |
| Storage | Stream to quarantine key; never to local disk beyond a temp file in a per-request dir; random object keys; original name stored as metadata only (sanitised, never used in paths) |
| Malware | ClamAV `clamd` scan job before any parsing; `infected`/`error` → file unusable, admin notified; signatures updated daily. Content clamd cannot fully scan (over `MaxFileSize`/`MaxScanSize`/`MaxRecursion`, encrypted, OLE2 macros) is reported as a finding (`AlertExceedsMax`, `AlertEncrypted`, `AlertOLE2Macros`), never as clean |
| Parsing sandbox | Parsers run in workers with CPU/memory/time limits; XLSX read-only mode, no macros, no external-link resolution, no formula evaluation, zip-bomb guard (max uncompressed size/entries ratio), XML entity expansion disabled (defusedxml); PDFs parsed with `pdfplumber`/`pypdfium2` with page and time caps; no JavaScript execution; OCR in a restricted container |
| Download | Presigned URL ≤ 5 min, `Content-Disposition: attachment` and an explicit `Content-Type`; audited. **S2 deviation:** a presigned GET cannot add `X-Content-Type-Options: nosniff` (SeaweedFS does not send it); `attachment` disposition means the browser saves rather than renders. Add the header at a proxy in front of the storage host when one exists (OD-3/OD-4) |
| Exports | **Formula-injection protection:** any exported cell beginning with `=`, `+`, `-`, `@`, tab, or CR is prefixed with `'` (text) in CSV/XLSX; numbers are written as typed numbers, text as strings |
| Retention | Raw files kept per workspace retention; deletion is an audited admin process |

## 5. Data protection

- **In transit:** TLS 1.2+ (Caddy automatic certificates), HSTS (`max-age=31536000; includeSubDomains`), internal service traffic on private Docker network; DB connections `sslmode=require` when not on the same host.
- **At rest:** encrypted volumes for Postgres and object storage; backups encrypted (client-side with a key stored separately); application-level encryption for MFA secrets and any stored third-party credentials (key from secret store, rotation procedure documented). Per-tenant encryption keys: Later.
- **Sensitive data inventory:** commercial terms (agreements), customer and pricing/cost data, remittances, user credentials. No payment card data or bank account numbers are stored in Release 1 (a validator rejects columns that look like card/bank numbers in imports: Luhn check on long numeric fields flagged).
- **Minimisation and logs:** logs contain ids, request ids, tenant id, action, status, latency; never request bodies, passwords, tokens, file contents, or amounts tied to named customers. Sentry scrubs headers/cookies/bodies.
- **Retention:** financial records 7 years default (workspace setting); audit log retained at least as long; deleted workspaces: 30-day grace then purge job (data + objects), with a deletion certificate record.
- **Privacy/legal:** data-processing agreement template, sub-processor list (hosting, LLM provider, email, Sentry), customer-visible AI-processing disclosure (OD-2). Legal review of customer contract and liability language is required before the first paid pilot (outside this repo).

## 6. API security

- Strict input validation (Pydantic strict, `extra=forbid`), output encoding (JSON only); parameterised queries only (no string-built SQL); ORM/Core with bound parameters; allow-listed sort/filter fields.
- Authentication on every endpoint except the explicit public list (register, login, reset, verify, accept-invite, health). A test enumerates routes and fails if a route is neither public-listed nor permission-declared.
- BOLA/IDOR: object lookups always include tenant context (RLS) and return `404` across tenants; tests iterate every route with other-tenant ids.
- Rate limiting: edge (Caddy) coarse limits + application limits (token bucket in Postgres or in-process per instance initially; Redis only via ADR).
- Mass assignment prevented by explicit request models; server-set fields (`status`, `created_by`, amounts) never accepted from clients.
- Security headers: CSP (`default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'`; no inline scripts, hashes/nonces for Vite build), `X-Frame-Options: DENY`, `Permissions-Policy` minimal.
- Error handling: generic 500s; no stack traces, SQL, or paths in responses.
- Dependency hygiene: lockfiles, `pip-audit`, `npm audit`, Dependabot (weekly, grouped per ecosystem; owner D-N8 2026-11-13; `.github/dependabot.yml`), SBOM from CI (Later), pinned base images by digest.
- SSRF: no user-supplied URLs are fetched by the server in Release 1 (a rule; any future fetch must use an allow-list).

## 7. Audit logging

See ADR-026 and `DATABASE_SCHEMA.md`. Required audit events (minimum): authentication events (login success/failure, MFA, lockout, reset), user/role/invitation/settings changes, file upload/download, imports (start, confirm, changes), agreement draft/submit/approve/reject/activate/supersede, calculation run start/complete, claim batch create/approve/export/submit/void, adjustments, remittance accept/reconcile/match confirm/reject, case transitions/write-offs, exports, support access, AI invocations (cost and outcome, no content), permission denials (rate-limited). Entries: actor, action, entity, before/after, request id, IP, UA, time. Written in the **same transaction** as the change; append-only; sealed with a hash chain by `audit_seal` (S11). Viewing audit is itself role-limited (`A P V`). **Sealing and verification (S11.1):** the tenant-scoped `audit_seal` job (hourly, self-rescheduling, chunked) appends to `audit_seals`; `python -m rebate.modules.audit.cli verify --tenant <id>` recomputes the chain and exits 1 at the first broken seal (altered or deleted entry, deleted seal); `retention-report` is the retention dry run. There is no key and the algorithm is public: anyone who can bypass the append-only trigger (the table owner or a superuser) can rewrite entries and re-seal them. Integrity is proven only back to the last seal hash kept off-box (`verify --anchor`); everything after the last anchor is tamper-evident only against people who cannot do that. Runbook: `infra/runbooks/audit-seal-verify.md`. Deletion of data past retention is not implemented (spec incomplete; owner decision in the S11.1 notes).

## 8. Secrets management

- Never in the repo, images, frontend bundles, logs, or CI output. `.env.example` has placeholders only; `.gitignore` covers `.env*`; pre-commit and CI run a secret scanner (`gitleaks`).
- Runtime: environment variables injected by the deploy process from a secret store (initially server-side `.env` with `0600` permissions owned by the deploy user; migrate to a secrets manager when the number of operators or environments grows).
- Distinct secrets per environment; rotation procedure documented in `DEPLOYMENT.md` (session secret, DB passwords, encryption key with key-id prefix for rotation, LLM key, SMTP, S3 keys).
- The DB `rebate_ai_writer` role has its own credentials.
- Frontend contains no secrets; configuration is public-only.

## 9. Backup and recovery

| Item | Requirement |
|---|---|
| Postgres | Nightly full logical backup + continuous WAL archiving (PITR) to a separate storage location/account; retention 35 days daily + 12 monthly; backups encrypted |
| Object storage | Versioning on, replication or periodic sync to a separate bucket/provider |
| Targets | RPO ≤ 15 minutes (WAL), RTO ≤ 4 hours (initial targets; confirm with owner) |
| Restore drills | **Mandatory before the first pilot and monthly after**; documented runbook with timing recorded in `PROJECT_STATUS.md` |
| Integrity | Backup job verifies completion and checksums; alert on failure/missed run |
| Per-tenant restore | Later (logical export per tenant), noted as a risk |

## 10. Resilience and failure handling

Jobs idempotent, bounded retries, dead-letter visibility with alerts; calculation runs invisible until `completed`; imports chunked and resumable; graceful degradation when AI/SMTP/ClamAV unavailable (uploads stay `pending`, UI explains; no data loss); health checks `live`/`ready`; DB connection pool limits and statement timeouts (default 30 s API, longer for workers); request body and concurrency limits.

## 11. Risks not obvious during development

1. **Prompt injection** via documents (see `AI_PROCESSING.md` §9).
2. **Formula injection** in exports.
3. **RLS + connection pooling** leaking tenant context across requests (always `SET LOCAL` inside transactions; test with a pool of size 1).
4. **Superuser/owner connections** bypass RLS (app must never connect as owner; startup check fails if `current_user` is a superuser/owner or has `BYPASSRLS`).
5. **Worker context loss** (job runs without tenant context or as owner).
6. **Backup scope** (all tenants in one backup; deletion requests must also age out of backups per policy).
7. **LLM provider retention** (questionnaires; OD-2).
8. **Timing/enumeration** on login/reset/invite.
9. **Zip bombs / XML expansion** in XLSX.
10. **Time zone / date-boundary errors** altering deadlines (financial impact).
11. **Support impersonation** without logging.
12. **Presigned URL leakage** (referrers, logs).
13. **Stale permissions** (cached roles); role is read per request.
14. **Float or locale parsing** corrupting amounts (security-adjacent integrity risk).

## 12. Security testing requirements

See `TESTING_STRATEGY.md` §9. Minimum before pilot: static analysis (bandit, ruff security rules, ESLint security plugin), dependency audit, secret scan, authorization matrix tests for every route and role, isolation suite, upload abuse tests (EICAR, renamed files, zip bomb, macro XLSX, oversized), session tests (fixation, expiry, revocation), CSRF tests, header checks, DAST (OWASP ZAP baseline) on staging, and an **external penetration test** before significant customer data is onboarded.

## 13. Day-one vs later

| Day one (Release 1) | Later |
|---|---|
| RLS + composite FKs + isolation tests; Argon2id; MFA; secure sessions; CSRF; RBAC + four-eyes; audit log; upload scanning/validation; TLS/HSTS/CSP; rate limits; secret scanning; tested backups/restore; structured logging with scrubbing | SSO/SCIM; per-tenant keys; support-grant tooling; WAF; SIEM; SOC 2 programme; SBOM and signed images; cross-region DR; per-tenant restore |
