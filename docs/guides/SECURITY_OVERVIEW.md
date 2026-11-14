# Security Overview for Customers

Veyra Recover is a multi-tenant web service for rebate and claim recovery. This page says what is built and tested today and what is not. It is drawn from the internal `docs/SECURITY.md`; internal secrets and settings are left out. Status date: 2026-11-13. Veyra is pre-pilot: **no production environment is running yet and no external security audit has been done.**

## Built and covered by automated tests

**Your data is separated from other customers.** Every customer table carries the workspace id and is protected by PostgreSQL row level security, which is enabled and forced. The application database role cannot bypass it, and the service refuses to start if it could. Background jobs set the workspace context before touching data. A test suite tries every route that takes a record id, plus list endpoints, jobs and file links, across the workspace boundary on every build. Another workspace's records return "not found".

**Sign-in.**
- Passwords of at least 12 characters, hashed with Argon2id, checked against a bundled list of common passwords.
- Two-factor sign-in (authenticator app) on by default, with one-time recovery codes. MFA secrets are encrypted in the database.
- After 5 failed attempts (password or MFA) the account locks for 15 minutes, longer on repeated lockouts. Login, reset and invitation responses do not reveal whether an account exists.
- Sessions use a random token in a secure, HttpOnly cookie; only a hash is stored. They expire after 30 idle minutes or 12 hours, and are revoked on logout, password reset and email change. CSRF tokens protect every change request.
- Changing an email address needs your password and a current MFA code.

**Access control.** Four roles (admin, approver, analyst, viewer), enforced on the server for every request, not only in the menu. A test lists all routes and fails if one lacks a permission rule. Role changes apply on the next request. Agreements need a second person to approve them when "four-eyes" is on (default).

**Uploads.** Only `.csv`, `.xlsx` and `.pdf`; extension, declared type and file content must agree; size limits apply. Files are stored under a random key, virus-scanned (ClamAV) before use, and unusable until clean. Spreadsheet parsing disables macros, formulas and external links. Downloads use short-lived links (5 minutes at most) issued only after a permission check, and are logged.

**Audit log.** Sign-ins, user and role changes, settings changes, file uploads and downloads, imports, agreement approvals, calculation runs and AI use are recorded with who, what and when, in the same database transaction as the change. Admins, approvers and viewers can read it.

**Web protections.** HTTPS with HSTS, a strict content security policy and frame blocking are set by the reverse proxy. Input is validated on the server. Logs avoid request bodies, passwords, tokens and file contents.

**AI.** AI only reads agreement documents and proposes a draft; it never decides eligibility or amounts, and a person approves every agreement. It is **off by default**: it needs your admin's opt-in and the operator's provider configuration. Document text is treated as untrusted (prompt-injection attempts are tested against a synthetic document). AI calls run through a restricted database role.

## Backups

Logical backup and restore scripts exist (not yet scheduled) and were run and timed on the development stack (2026-11-13). Not yet in place: scheduled production backups, encrypted off-site copies, point-in-time recovery (written as configuration only, never run), and a restore drill on production-sized data. Targets under discussion: recovery point 15 minutes, recovery time 4 hours. These are targets, not commitments.

## Not in Release 1 yet, or not yet done

- External penetration test (required before significant customer data is onboarded).
- SOC 2 or other certification; SSO and SCIM; per-customer encryption keys; per-customer restore.
- Tamper-evident sealing of the audit log (planned).
- Customer-visible data-processing agreement, sub-processor list and legal review of terms: pending.
- AI provider terms are not approved; AI processing stays disabled until they are.
- Exports of claim batches, with spreadsheet formula-injection protection, are not built yet because claim batches are not built yet.
- Downloads do not yet carry `X-Content-Type-Options: nosniff` (known issue K-10; files are served as attachments).
- Production hosting, monitoring and alerting are not set up. At-rest encryption of disks and object storage depends on the chosen host and is not yet verified.
- Support staff have no standing access to customer data; a time-limited, audited support access feature is not built.

Report a suspected vulnerability to your Veyra contact. A contact address and disclosure process are not yet published.
