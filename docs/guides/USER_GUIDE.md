# User Guide

For people using Veyra Recover day to day. This describes what exists on `main` (2026-11-13). Screens for claim batches, remittances, recovery cases and reports (stages S7 to S10) are **not in Release 1 yet**; they are listed at the end.

Veyra helps a distributor find money owed by manufacturers. **The software calculates; AI never decides eligibility or amounts.** AI only reads documents and proposes a draft that a person reviews.

## 1. Roles

| Role | Can do |
|---|---|
| admin | Everything: users, invitations, settings, jobs, plus all below |
| approver | Approve agreements, edit manufacturer settings, read the audit log, plus analyst rights |
| analyst | Upload files, run imports, edit master data and mappings, draft agreements, run calculations, start AI extractions |
| viewer | Read data, settings and the audit log; change nothing |

The menu only shows what your role may open, and the server checks again on every action. When "four-eyes" is on (default), the person who created or last edited an agreement draft cannot approve it. Source: `backend/src/rebate/modules/tenancy/policies.py`.

## 2. Signing in

- **Create your workspace** on the Register page (company name, your email) and verify the email link. Your user becomes admin.
- Passwords need at least 12 characters and are checked against a list of common passwords.
- **MFA** (authenticator app) is required by default. At first sign-in you set it up and get 10 one-time recovery codes: store them safely. Admins cannot reset MFA in the app; ask your operator (`infra/runbooks/locked-out-admin.md`).
- Sessions end after 30 idle minutes or 12 hours.
- Forgot password: use the link on the login page. After 5 failed attempts (password or MFA) the account locks for 15 minutes, longer on repeated lockouts.
- Invitations: an admin invites by email (Invitations page); you accept through the link. Password, email change and recovery codes are under Account security.

## 3. Files

Files page: upload imports as `.csv` or `.xlsx`, agreements as `.pdf` or `.xlsx` (spreadsheets up to 50 MB, PDF up to 25 MB). Every file is virus-scanned first and is usable only when its status is clean. Other file types are refused.

## 4. Master data

Under Master data: manufacturers, customers, products, units of measure (conversions) and branches. Analysts and above can add and edit; manufacturer settings (claim window days and basis, variance tolerance) are edited by approvers or admins. Aliases map the names that appear in your files to one customer or product. Names that could not be matched land in **Unmapped**, where you resolve them.

## 5. Imports

Imports bring in sales, purchases, customers, products and unit conversions. What to prepare: `ONBOARDING_PLAYBOOK.md`.

1. Imports, New import: pick or upload the file and the import type.
2. Map your columns to Veyra fields. Save the mapping as a template; files with the same headers reuse it. All cells are read as text, so SKUs such as `00418` keep their leading zeros.
3. Check the preview and the row errors (row, column, code, message). Fix the file and upload again, or choose to import only the valid rows.
4. The import runs in the background. Re-importing the same file changes nothing; a changed row is recorded as a change, never silently overwritten.
5. Sales and purchase lines are browsable under Lines.

## 6. Agreements

Agreements (SPA price-difference deals and rebates) never go live directly. Every new version starts as a **draft**: from the manual form, from a spreadsheet of SPA items, or from an AI extraction.

1. Create a draft (Agreement drafts): manufacturer, dates, priority, date basis, calculation basis, customer and product scope, price-check rules, tiers for rebates.
2. Validate, and fix what the validator lists.
3. Optionally **preview the calculation**: a read-only trial against imported lines.
4. Submit. An approver or admin approves, rejects or returns it. Approving creates an **immutable** version. Changing an active agreement creates a new version and splits the affected period (rules: `docs/BUSINESS_LOGIC.md`). You can compare two versions and see the history.
5. Versions expire automatically after their end date.

## 7. Extraction review (AI)

Extractions page: start an extraction on an uploaded PDF or spreadsheet. It works only if your admin enabled AI document reading in Settings **and** the operator configured a provider. Otherwise the extraction ends as "needs manual entry" and an empty draft is created for you to fill in. When AI runs, each extracted field carries its evidence (page and text), two independent passes are compared, and missing or uncertain values are left empty rather than guessed. The result is an ordinary draft that follows section 6, so a person always approves. AI usage and cost are under Extractions, Usage.

## 8. Calculations

Calculations page: start a run for a period. The engine applies approved agreements to imported sales and purchase lines with exact decimal arithmetic. Each result is eligible, exception (with a reason code such as `price_mismatch`, `outside_claim_window`, `missing_uom_conversion`), netted, or adjustment, and the result page shows the rule and numbers behind it. Rebates show accruals and a tracker for tier progress. A run is visible only once completed.

## 9. Admin pages

Users, Invitations, Settings, Audit log (who did what, when) and Jobs (see `OPERATOR_GUIDE.md`).

## 10. Not in Release 1 yet

Claim batches (create, approve, export, submit), remittance upload and matching, recovery cases and write-offs, reports and dashboards, expiry alert emails, SSO. The roles above already define who will be allowed to do them.
