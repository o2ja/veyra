# Product Requirements

> Scope: **Release 1** (Stages S0–S11). Priorities: **P0** = Release 1 cannot ship without it; **P1** = Release 1 target, may slip one stage; **R2** = Release 2, not approved for implementation.
> Terminology and formulas are defined in `BUSINESS_LOGIC.md`. Schema in `DATABASE_SCHEMA.md`. Endpoints in `API_SPECIFICATION.md`.

## 1. Functional requirements

### 1.1 Workspace, identity, access (module `identity`, `tenancy`)

| ID | Requirement | Pri |
|---|---|---|
| FR-ID-01 | A company can register a workspace; the registrant becomes `admin`. | P0 |
| FR-ID-02 | Admins invite users by email with a role; invitations expire (7 days) and are single-use. | P0 |
| FR-ID-03 | Email + password login (Argon2id) with TOTP MFA; MFA enforcement is a workspace setting, default **on**. | P0 |
| FR-ID-04 | Four roles (`admin`, `approver`, `analyst`, `viewer`) with the permission matrix in `SECURITY.md` §3. | P0 |
| FR-ID-05 | Admins can deactivate users; sessions are revoked immediately. | P0 |
| FR-ID-06 | Password reset via single-use, expiring token; no account enumeration. | P0 |
| FR-ID-07 | A user may belong to several workspaces and switch between them. | P1 |
| FR-ID-08 | Workspace settings: business time zone, MFA enforcement, four-eyes policy, AI processing opt-in/out, default currency (USD). | P0 |

### 1.2 Audit (module `audit`)

| ID | Requirement | Pri |
|---|---|---|
| FR-AU-01 | Every state change to financial, agreement, user, and settings records writes an append-only audit entry (actor, action, entity, before/after, request id, IP, timestamp). | P0 |
| FR-AU-02 | Audit entries cannot be modified or deleted through the application role. | P0 |
| FR-AU-03 | Audit log is searchable by entity, actor, date range, action. | P0 |
| FR-AU-04 | Tamper-evident hash chain sealing job. | P1 |

### 1.3 Files (module `files`)

| ID | Requirement | Pri |
|---|---|---|
| FR-FI-01 | Uploaded files are stored in object storage, hashed (SHA-256), malware-scanned, and quarantined until clean. | P0 |
| FR-FI-02 | Allowed types: `.csv`, `.xlsx`, `.pdf`, with extension, declared MIME, and magic-byte agreement. `.xlsm`, `.xls`, executables, and archives are rejected. | P0 |
| FR-FI-03 | Size limit per type (configurable; defaults CSV/XLSX 50 MB, PDF 25 MB). | P0 |
| FR-FI-04 | The original file is retained and linked from every record derived from it. | P0 |

### 1.4 Master data and import (modules `masterdata`, `ingestion`)

| ID | Requirement | Pri |
|---|---|---|
| FR-IM-01 | Import types: sales documents (invoices + credit notes), purchase documents (receipts + vendor credits), customers, products (with cost), UoM conversions. | P0 |
| FR-IM-02 | CSV and XLSX parsing reads **all cells as text**; typing happens in the normaliser (no float coercion, leading zeros preserved, locale-aware numbers). | P0 |
| FR-IM-03 | Column mapping UI: user maps file columns to canonical fields; the mapping is saved as a named template and reused by header signature. AI may *suggest* a mapping (human confirms). | P0 |
| FR-IM-04 | Validation produces row-level errors (row number, column, code, message). An import with errors can be fixed and re-uploaded, or accepted partially **only if** the user explicitly chooses "import valid rows". | P0 |
| FR-IM-05 | Duplicate prevention: file hash, natural row key, row content hash (see `DATABASE_SCHEMA.md` §5). Re-importing the same file is a no-op. A changed row creates a new row version and a flagged change; it is never silently overwritten. | P0 |
| FR-IM-06 | Import history: who, when, file, template, counts (inserted/unchanged/changed/rejected), status. | P0 |
| FR-IM-07 | Imports run in background jobs; they are idempotent and resumable. | P0 |
| FR-IM-08 | Credit-note / return lines reference the original invoice line (document number + line, or by user-confirmed match). | P0 |
| FR-IM-09 | Customer/product/manufacturer matching to master data with alias handling and an "unmapped" review queue. | P0 |

### 1.5 Agreements (module `agreements`, `extraction`)

| ID | Requirement | Pri |
|---|---|---|
| FR-AG-01 | Agreement types: `spa` (price-difference and other bases) and `rebate` (percentage/flat/tiered). Rules per `BUSINESS_LOGIC.md` §3–§5. | P0 |
| FR-AG-02 | **One pipeline:** every new agreement version starts as an `agreement_draft` regardless of source (`manual`, `spreadsheet_template`, `ai_extraction`). Same schema, same validator, same approval gate. | P0 |
| FR-AG-03 | Manual entry form for all rule fields, with inline validation and a "preview calculation" against a sample of imported lines (read-only). | P0 |
| FR-AG-04 | Bulk SPA items (SKU, customer scope, deviated cost, reference cost, sell price, quantity cap) from an Excel/CSV template using the import pipeline. | P0 |
| FR-AG-05 | AI extraction from PDF/XLSX/CSV produces a draft where every field carries evidence (page + text span or cell). | P0 |
| FR-AG-06 | Review screen: source on the left, extracted fields on the right, evidence highlighting, unresolved/low-confidence fields called out. | P0 |
| FR-AG-07 | Activation requires `approver` or `admin`; the reviewer and time are recorded. If four-eyes policy is on, the approver must differ from the creator. | P0 |
| FR-AG-08 | Active versions are **immutable**. Amendments create a new version (including retroactive amendments) using split-supersede (`BUSINESS_LOGIC.md` §9). | P0 |
| FR-AG-09 | Version history and field-level diff between versions. | P0 |
| FR-AG-10 | Agreement lifecycle states: `draft`, `pending_approval`, `active`, `superseded`, `expired`, `cancelled`, `rejected`. | P0 |
| FR-AG-11 | SPA quantity cap and consumption tracking. | P0 |
| FR-AG-12 | Expiry alerts for agreements ending within N days. | P1 |

### 1.6 Claims engine (module `claims`)

| ID | Requirement | Pri |
|---|---|---|
| FR-CL-01 | A calculation run takes a scope (manufacturer(s), date range, as-of datetime) and pins: data cut-off, agreement version ids, engine version, config hash. | P0 |
| FR-CL-02 | Per source line, the engine outputs `eligible` or an exception code, the claim amount, and a **trace** (ordered steps with inputs, intermediate values, rounding). | P0 |
| FR-CL-03 | Results are append-only. Re-running creates a new run; old results remain. | P0 |
| FR-CL-04 | Missed-claims scan: lines eligible under an active agreement and not present in any non-voided claim line. Output is a downloadable report (CSV/XLSX) and a UI list with totals. | P0 |
| FR-CL-05 | Exceptions are first-class: `no_agreement`, `expired_agreement`, `ineligible_customer`, `ineligible_product`, `price_mismatch`, `conflicting_agreements`, `cap_exhausted`, `missing_cost`, `missing_uom_conversion`, `outside_claim_window`, and others in `BUSINESS_LOGIC.md` §11. | P0 |
| FR-CL-06 | Retroactive tier accrual ledger (append-only deltas) with catch-up and claw-back entries. | P0 |
| FR-CL-07 | Recalculation after an agreement change produces a **proposed adjustment set** compared with frozen claim lines. Nothing is changed until approved. | P0 |
| FR-CL-08 | Click-through "explain this number" from any amount to the trace and source rows. | P0 |

### 1.7 Claim batches (module `claims`)

| ID | Requirement | Pri |
|---|---|---|
| FR-CB-01 | Build a claim batch for one manufacturer from selected eligible results. | P0 |
| FR-CB-02 | Batch states: `draft`, `approved`, `submitted`, `void`. Submission freezes lines (DB-enforced). | P0 |
| FR-CB-03 | Export to CSV/XLSX through a configurable per-manufacturer **column-mapping export template** (no code). | P0 |
| FR-CB-04 | The user records submission date and external reference (portal ticket, email). Submission does not transmit anything to the manufacturer. | P0 |
| FR-CB-05 | Post-submission corrections are `claim_adjustment` records (positive or negative) included in later batches. | P0 |
| FR-CB-06 | Claim window deadlines computed per manufacturer setting; lines near or past the window are flagged. | P0 |
| FR-CB-07 | Manufacturer-specific claim file builders with custom logic. | R2 |

### 1.8 Remittance and reconciliation (module `reconciliation`)

| ID | Requirement | Pri |
|---|---|---|
| FR-RC-01 | Upload remittance documents (credit memo, remittance advice, deduction notice) as CSV/XLSX/PDF. PDF goes through extraction with arithmetic validation and human confirmation. | P0 |
| FR-RC-02 | Payment channel recorded: `credit_memo`, `check`, `ach`, `deduction`. | P0 |
| FR-RC-03 | Deterministic tiered matching (`BUSINESS_LOGIC.md` §10). Auto-confirm only exact tiers. Everything else is `suggested` until a human confirms. | P0 |
| FR-RC-04 | Side-by-side view: claimed vs paid per line with status and reason. | P0 |
| FR-RC-05 | Partial payments and many-to-many allocation with explicit allocated amounts that must sum. | P0 |
| FR-RC-06 | Every difference becomes a `variance_case` with a reason code from a controlled list. Manufacturer reason codes are mapped through `reason_code_mappings`. | P0 |
| FR-RC-07 | Unmatched remittance lines (payment with no claim) are surfaced as cases. | P0 |
| FR-RC-08 | Reconciliation can be re-run after correction; confirmed matches are not silently changed. | P0 |

### 1.9 Recovery (module `recovery`)

| ID | Requirement | Pri |
|---|---|---|
| FR-RV-01 | Variance case: reason, amount, deadline, owner, status, notes, attachments, full event history. | P0 |
| FR-RV-02 | Statuses: `open`, `to_reclaim`, `submitted`, `recovered`, `written_off`, `closed_no_action`. | P0 |
| FR-RV-03 | Deadline countdown and sorting by "money at risk soonest". | P0 |
| FR-RV-04 | Write-off requires a reason and `approver`/`admin` approval (four-eyes aware). | P0 |
| FR-RV-05 | Deterministic dispute assessment: re-evaluate the original claim line against the agreement and state whether it is `supported` or `weak`, with the failing rule. | P1 |
| FR-RV-06 | In-app notifications for deadlines (14/7/3 days) and new short-pays; daily email digest. | P1 |
| FR-RV-07 | Escalation chains, SLA rules. | R2 |

### 1.10 Reporting (module `reporting`)

| ID | Requirement | Pri |
|---|---|---|
| FR-RP-01 | Dashboard: claimed, collected, short-paid, recovered, written-off, at-risk, expiring soon; filter by manufacturer, month, branch. | P0 |
| FR-RP-02 | Rebate tier tracker: YTD qualifying volume vs tier thresholds, distance to next tier, accrued vs earned. | P1 |
| FR-RP-03 | Audit-ready trace export per claim batch (PDF/XLSX): invoice line → agreement version → claim → payment. | P1 |
| FR-RP-04 | Tier-crossing simulation, scorecards, forecasting, benchmarks, suggested journal entries. | R2 |

## 2. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-01 | Accuracy | All monetary values are `NUMERIC` in the DB and `Decimal` in code. No floats on any money path. Verified by lint rule and tests. |
| NFR-02 | Reproducibility | The same inputs + engine version always produce byte-identical results and trace (golden tests). |
| NFR-03 | Isolation | Cross-tenant read/write is impossible via API, jobs, files, exports; enforced in application *and* database (RLS). Automated isolation suite runs on every CI build. |
| NFR-04 | Performance | Import of 1M sales lines completes in a background job < 30 min on the reference VPS; calculation run over 1M lines < 15 min; interactive list endpoints p95 < 500 ms at 5M lines per tenant with correct indexes. *(Targets to be verified in S11; adjust with evidence.)* |
| NFR-05 | Reliability | Jobs are idempotent with bounded retries and a visible dead-letter state. Daily backups, PITR, **restore drill documented and executed** before pilot. |
| NFR-06 | Security | Controls in `SECURITY.md`. OWASP ASVS L2 as the review baseline. |
| NFR-07 | Observability | Structured logs with request id and tenant id (never PII or secrets), error tracking, failed-job alerts. |
| NFR-08 | Usability | Primary users are accountants. Task flows in ≤ 3 clicks from the dashboard to a case; no jargon in UI copy; keyboard-friendly tables. |
| NFR-09 | Accessibility | WCAG 2.1 AA target for core flows. |
| NFR-10 | Portability | No cloud-specific services in the core. S3-compatible storage, standard Postgres, container deployment. |
| NFR-11 | Maintainability | Module boundaries enforced by `import-linter`; typed Python (`mypy --strict` on engine and matching packages); ≥ 90% line coverage on engine and matching, ≥ 80% overall. |
| NFR-12 | Data retention | Financial records retained 7 years by default; configurable per workspace; deletion is a controlled, audited process. |
| NFR-13 | Browser support | Current and previous major versions of Chrome, Edge, Safari, Firefox. Desktop-first; tablets supported; phones read-only acceptable. |
| NFR-14 | Time | Timestamps in UTC; date-only fields stored as `date`; deadlines computed in the workspace business time zone. |
| NFR-15 | Internationalisation | English UI; USD only; US date/number display. Schema carries `currency_code` on money rows so multi-currency is possible later without migration of meaning. |

## 3. User stories and acceptance criteria

Format: **US-xx** story, then acceptance criteria (AC) in Given/When/Then style where useful.

### Workspace and access

**US-01** As a finance lead I register my company so my team can work in a private workspace.
- AC: Registering creates a tenant, an `admin` membership, an audit entry; a second registration with the same email is rejected without revealing whether the email exists (generic message on public endpoints).

**US-02** As an admin I invite an analyst and a read-only CFO.
- AC: Invite email contains a single-use link; accepting sets password and MFA; the invite cannot be reused or accepted after expiry; role changes appear in the audit log.

**US-03** As an admin I deactivate a former employee.
- AC: All their sessions end within one request cycle; their historical audit entries remain and still name them.

### Import

**US-04** As an analyst I upload a sales export and map its columns once.
- AC: Given a CSV with header signature X, when I upload it a second time, then the saved template is auto-selected. Given a leading-zero SKU `00123`, then it is stored as `00123`. Given the same file twice, then the second import reports 0 inserted, N unchanged.

**US-05** As an analyst I see which rows failed and why.
- AC: Each error lists row number, column, code, message; a downloadable error file mirrors the original with an `error` column; no partial import happens unless I choose it explicitly.

**US-06** As an analyst I re-export a corrected file where 3 invoice lines changed.
- AC: The 3 lines produce new versions and a "changed source rows" report; if any affected line is already in a submitted claim, a flag is raised and a review item is created.

### Agreements

**US-07** As an analyst I create an SPA by hand.
- AC: Required fields are validated server-side; overlapping validity for the same scope is rejected on activation; saving creates a `draft`; I cannot activate it myself if four-eyes is on.

**US-08** As an analyst I upload a manufacturer's PDF and get a draft.
- AC: Every populated field shows its evidence; fields without evidence are empty and flagged; a failed or low-confidence extraction ends in `needs_manual_entry` with a clear message and never blocks other work; the draft has **no effect on calculations** until approved.

**US-09** As an approver I review and approve an extracted agreement.
- AC: I see source and fields side by side; I can edit any field; approval creates an immutable version, records me and the time, and writes audit entries; rejecting records a reason.

**US-10** As an approver I record a retroactive amendment.
- AC: Given a version covering Jan 1–Dec 31, when I amend terms effective Jul 1, then the system produces v1 (Jan 1–Jun 30, same terms), v2 (Jul 1–Dec 31, new terms), v1-original marked `superseded`; **no existing calculation result or submitted claim line is altered**; a recalculation proposal lists the differences.

### Claims

**US-11** As an analyst I run the missed-claims scan for a manufacturer and period.
- AC: Output lists each eligible-unclaimed line with amount, agreement version, and trace; totals reconcile to the sum of lines; the same inputs reproduce the same totals; exceptions are listed separately with reason codes.

**US-12** As an analyst I click an amount and see where it came from.
- AC: Drill-down shows source file/row, normalised line, agreement version, matched rule, formula with substituted values, rounding step, claim batch, and payment if any.

**US-13** As an approver I submit a claim batch.
- AC: After submission, lines cannot be edited by any API or direct DB update through the app role; corrections appear as adjustments.

### Reconciliation and recovery

**US-14** As an analyst I upload a credit memo and see matched vs unmatched.
- AC: Exact-key matches are confirmed automatically; fuzzy matches appear as suggestions with a score and the fields that matched; confirming records me; the sum of allocations per remittance line equals its amount.

**US-15** As an analyst I work the recovery queue.
- AC: Queue sorts by nearest deadline; each case shows reason, amount, deadline, claim line, agreement, and dispute assessment; status changes are logged as events.

**US-16** As an approver I write off a case.
- AC: Requires reason; four-eyes enforced; writes audit entry; the case's amount moves from "at risk" to "written off" on the dashboard.

### Reporting

**US-17** As a CFO (`viewer`) I see claimed vs collected vs at-risk, and drill into a manufacturer.
- AC: Dashboard totals equal the sum of underlying rows (tested); every figure links to its list.

## 4. Business rules (summary)

Authoritative text is in `BUSINESS_LOGIC.md`. Highlights that drive requirements:

1. AI never decides eligibility or amounts.
2. A submitted claim line is immutable; corrections are adjustments.
3. Agreement versions are immutable once active.
4. Quantity caps and tier volumes are computed deterministically in a defined order.
5. SPA lines may qualify for a tier threshold without earning the payout (two flags).
6. Dates compared are those the agreement names (`invoice_date` or `ship_date`).
7. Deadlines are calendar-day arithmetic with no weekend rolling (conservative).
8. Every variance has exactly one reason code.

## 5. Explicitly excluded from Release 1

| Excluded | Why |
|---|---|
| ERP connectors / live sync | Not needed to prove value; high integration cost |
| EDI 844/849, vendor portal submission | Needs trading-partner certification; build on demand |
| Posting to customer GL / accrual write-back | Accounting liability; judgment belongs to customer's controller |
| Suggested journal entries export | R2 |
| Commission calculation or clawback | Out of scope; data may be exposed later |
| Pricing optimisation, price-list management | Different product |
| Predictive ML (acceptance prediction) | Insufficient data |
| Multi-currency | A-08; schema ready, logic not |
| Mobile app | Desktop-first |
| SSO/SCIM | R2 (enterprise requirement when asked) |
| Manufacturer-direct integrations | Not in plan |
