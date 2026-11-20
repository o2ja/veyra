# User Guide

For people using Veyra Recover day to day. This guide covers the complete workflow from setup, agreements and calculations through missed claims, batches, remittances, recovery cases and executive reporting.

Veyra helps a distributor find money owed by manufacturers. **The software calculates; AI never decides eligibility or amounts.** AI only reads documents and proposes a draft that a person reviews.

## 1. Roles

| Role | Can do |
|---|---|
| admin | Everything: users, invitations, settings, jobs, plus all below |
| approver | Approve agreements, edit manufacturer settings, read the audit log, approve/submit/void claim batches, approve adjustments, write off recovery cases, export audit packs, plus analyst rights |
| analyst | Upload files, run imports, edit master data and mappings, draft agreements, run calculations, start AI extractions, create and export claim batches, propose adjustments, upload and reconcile remittances, assign and work recovery cases, propose write-offs, refresh reports |
| viewer | Read data, settings, reports, recovery cases, remittances, claim batches and the audit log; export audit packs; change nothing |

The menu only shows what your role may open, and the server checks again on every action. When "four-eyes" is on (default), the person who created or last edited an agreement draft cannot approve it. Similarly, the person who created a claim batch, proposed an adjustment, or proposed a recovery case write-off cannot approve it. Source: `backend/src/rebate/modules/tenancy/policies.py` and `docs/SECURITY.md §3`.

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

Extractions page: start an extraction on an uploaded PDF or spreadsheet. It works only if your admin enabled AI document reading in Settings **and** the operator configured a provider. Otherwise the extraction ends as "needs manual entry" and an empty draft is created for you to fill in. When AI runs, each extracted field carries its evidence (page and text), two independent passes are compared, and missing or uncertain values are left empty rather than guessed. The result opens on the **Contract Review** page: a summary with counts, the items that need your decision (each editable right next to the quote from the contract), and a collapsed list of what was found. Anything the contract does not state, such as the date basis or rounding, is left empty for you to choose; you cannot submit until every flagged item is resolved. Technical details for support are collapsed at the bottom. The result is an ordinary draft that follows section 6, so a person always approves. AI usage and cost are under Extractions, Usage.

## 8. Calculations

Calculations page: start a run for a period. The engine applies approved agreements to imported sales and purchase lines with exact decimal arithmetic. Each result is eligible, exception (with a reason code such as `price_mismatch`, `outside_claim_window`, `missing_uom_conversion`), netted, or adjustment, and the result page shows the rule and numbers behind it. Rebates show accruals and a tracker for tier progress. A run is visible only once completed.

## 9. Admin pages

Users, Invitations, Settings, Audit log (who did what, when) and Jobs (see `OPERATOR_GUIDE.md`).

## 10. Missed claims

Missed claims surfaces eligible rebate and SPA amounts identified by calculation runs that have not yet been packaged into a submitted claim batch.

### Who may do it

- **Analyst**, **Approver**, **Admin**: Apply filters, export lists, and initiate batch creation (`claim_batch.create`, `claim_batch.export`).
- **Viewer**: Read-only access to missed claim lines, summary figures, and export lists (`data.read`).

### What the screen shows

Navigate to **Claims** in the main sidebar. The subnavigation bar (`frontend/src/features/claims/common.tsx`) provides four tabs:
- `Missed claims` (active)
- `Claim batches`
- `Adjustments`
- `Export templates`

The screen is structured into three primary panels:

1. **Filter bar (`Missed claims filters`)**:
   - Filter controls: **Manufacturer** (single select dropdown), **Agreement** (dropdown filtered by selected manufacturer), **Period start** and **Period end** (date pickers), **Status** multiselect, **Minimum amount**, and **Grouping mode** (toggle between line-item claim lines and rebate accruals).
   - Filter action buttons:
     - `Apply filters`: Re-queries missed claims based on criteria.
     - `Reset filters`: Clears all filters back to defaults.
     - `Export list (CSV)`: Downloads filtered claimable lines as a `.csv` file.
     - `Export list (XLSX)`: Downloads filtered claimable lines as an `.xlsx` workbook.

2. **Summary strip (`Missed claims summary`)**:
   - `Total claimable lines`: Total line count and cumulative currency value across eligible unbatched lines.
   - `Expiring soon (≤ 30 days)`: Lines whose claim window expires within 30 calendar days.
   - `Overdue (past claim window)`: Lines past their claim deadline, requiring an exception or approval before submission.
   - `Adjustments to carry`: Count and net monetary value of approved adjustments waiting to be included in the next batch for this manufacturer.

3. **Data tables & batch creation**:
   - In standard lines mode, the table **`Missed claim lines`** lists individual lines with selection checkboxes, Calculation run ID, Agreement reference, Product code, Customer name, Invoice date, Days until deadline, and Claimable amount.
   - Batch creation action buttons above the table:
     - `Create batch from selected`: Enabled when at least one line checkbox is checked. Opens the batch creation modal.
     - `Create batch from all matching filter`: Packages all claimable lines matching the active filter into a new batch.
   - In accruals mode, the table **`Rebate accrual entries`** aggregates earned rebate tiers, with the button `Create batch from selected accruals`.

### Claim window and exceptions

The claim window is governed by manufacturer master data (`claim_window_days` and `claim_date_basis`, e.g., 60 days from invoice date or 90 days from calendar month end). Lines that fail validation are cataloged under the exceptions summary with reasons such as:
- `outside_claim_window`: The transaction date exceeds the manufacturer claim deadline.
- `already_claimed`: Line was previously claimed in another active or submitted batch.
- `missing_product_mapping`: SKU lacks an approved manufacturer alias.

### Real data example

From `testdata/generated/ground_truth/claim_batches_fixture.json`:
- Manufacturer: **Voltara Electric** (`VOLT`).
- Period: June 2026 calculation run.
- Unbatched claimable lines: 7 lines totaling **`10,410.00`**:
  - `CL-2026-000001`: `2,200.00`
  - `CL-2026-000002`: `3,120.00`
  - `CL-2026-000003`: `2,080.00`
  - `CL-2026-000004`: `250.00`
  - `CL-2026-000005`: `1,000.00`
  - `CL-2026-000006`: `1,100.00`
  - `CL-2026-000007`: `660.00`
- Available approved adjustments: `ADJ-2026-0001` for **`-50.00`** (customer return reversal on scenario `SC-09r`).

### Common errors and backend codes

- **Selecting multiple manufacturers**: `400` `multiple_manufacturers` ("All selected lines must belong to the same manufacturer").
- **Lines already in active batch**: `409` `lines_already_batched` ("One or more lines are already part of an active claim batch").
- **Empty batch creation**: `400` `empty_batch` ("At least one line or accrual entry must be selected").

---

## 11. Claim batches

Claim batches assemble approved claimable lines and carried adjustments into a single formal submission packet for a manufacturer. Batches enforce four-eyes governance before export or submission.

### Who may do it

- **Analyst**: Create batches from missed claims, initiate file exports (`claim_batch.create`, `claim_batch.export`). Analysts *cannot* approve, submit, or void batches.
- **Approver**, **Admin**: Approve batches, submit batches, void batches (`claim_batch.approve`, `claim_batch.submit`, `claim_batch.void`).
- **Four-eyes rule**: The user who created a batch cannot approve it. A second approver or admin must review and approve (`403` `four_eyes_violation`).
- **Viewer**: Read-only access to batch lists, detail views, and exported files (`data.read`).

### What the screens show

#### Batch list (`/claims/batches`)
Under the tab `Claim batches`, a searchable and sortable table displays:
- Batch number (e.g. `CB-VOLT-2026-06`)
- Manufacturer name and code
- Period covered
- Status badge: `draft`, `approved`, `exported`, `submitted`, or `voided`
- Line count and Carried adjustments count
- Net batch total
- Created by and Created at

#### Batch detail page (`/claims/batches/:batchId`)
Selecting a batch opens `claim-batch-detail-page.tsx`:
- **Header**: Displays Batch number, Manufacturer, Status badge, and lifecycle timestamps.
- **Action bar**:
  - `Approve batch`: Approves the draft batch. Visible to Approvers and Admins.
  - `Export batch`: Generates the claim file using an export template.
  - `Submit batch`: Records transmission to the manufacturer and transitions status to `submitted`.
  - `Void batch`: Voids the batch and releases lines back to the missed claims pool.
- **Summary cards**:
  - `Batch total (lines + carried adjustments, from server)`: Authoritative net total computed by backend exact decimal arithmetic.
  - Total line items count and carried adjustment count.
  - Export status card: When exported, displays the template name, generation timestamp, and button `Download exported claim`.
- **Detail tabs**:
  - Claim lines: Individual line items with agreement references, product codes, quantities, and claimed values.
  - Carried adjustments: List of prior-period adjustments netted into this batch.

### Step-by-step workflow

1. **Create batch**:
   - In `Missed claims`, select claim lines and click `Create batch from selected` or `Create batch from all matching filter`.
   - Enter a batch reference and period in the modal dialog, then click `Create batch`.
   - The batch is created in `draft` status. Carried adjustments for that manufacturer are automatically attached by the server.

2. **Four-eyes approval**:
   - If the user viewing the batch created it, the action bar displays the banner:  
     `Four-eyes rule: you created this batch, so a second approver must approve it.`  
     The button `Approve batch` is hidden or disabled for that user.
   - A distinct Approver or Admin opens the batch, verifies the lines, and clicks `Approve batch`.
   - Status changes from `draft` to `approved`.

3. **Export batch with template**:
   - Click `Export batch`. A modal opens showing configured templates (CSV or Excel formats tailored to manufacturer portal specifications).
   - Click `Start export`. An asynchronous background job generates the file.
   - When complete, status moves to `exported`, and the button `Download exported claim` becomes active.

4. **Submit batch**:
   - Once the claim is dispatched to the manufacturer, an Approver or Admin clicks `Submit batch`.
   - The dialog `Confirm submission` prompts for the mandatory **Submission date** (defaults to today's date).
   - Click `Confirm submission`. Status moves to `submitted`. Submitted lines are permanently locked against editing or re-batching. The manufacturer's expected payment lag clock begins ticking.

5. **Void batch**:
   - If an error is detected prior to payment, an Approver or Admin clicks `Void batch`.
   - A confirmation dialog `Confirm void` requests a required void reason.
   - Click `Confirm void`. Status transitions to `voided`. All lines and carried adjustments are immediately unlinked and returned to `Missed claims`.

### Real data example

From `testdata/generated/ground_truth/claim_batches_fixture.json`:
- Batch reference: `CB-VOLT-2026-06` for Voltara (`VOLT`).
- Claim lines sum: **`10,410.00`** across 7 lines (`CL-2026-000001` through `CL-2026-000007`).
- Carried adjustment: `ADJ-2026-0001` for **`-50.00`**.
- Net batch total: `Batch total (lines + carried adjustments, from server)` = **`10,360.00`**.
- Submitted date: `2026-07-05`.

### Common errors and backend codes

- **Four-eyes violation**: `403` `four_eyes_violation` ("A user cannot approve a claim batch they created").
- **Invalid lifecycle transition**: `409` `invalid_batch_status` ("Batch cannot be transitioned from status '{current_status}' to '{target_status}'").
- **Missing submission date**: `400` `missing_submission_date` ("Submission date is required when submitting a batch").
- **Negative net batch total**: `400` `negative_batch_total` ("Net batch total cannot be negative unless the manufacturer allows negative claims").

---

## 12. Adjustments

Adjustments allow distributors to record manual corrections, return clawbacks, or recalculation differences that offset future claim submissions.

### Who may do it

- **Analyst**: Propose manual adjustments (`New adjustment`).
- **Approver**, **Admin**: Approve (`Approve`) or reject (`Reject`) proposed adjustments (`adjustment.approve`).
- **Four-eyes rule**: The user who proposed an adjustment cannot approve it (`403` `four_eyes_violation`).
- **Viewer**: Read-only access to adjustments list and audit details (`data.read`).

### What the screen shows

Navigate to the tab `Adjustments` under **Claims** (`frontend/src/features/claims/claim-adjustments-page.tsx`).
- Action button at top right: `New adjustment`.
- Filter controls: Filter by Manufacturer, Agreement, Status (`pending`, `approved`, `rejected`, `carried`), and Creation date range.
- Adjustments table columns:
  - Adjustment ID (e.g. `ADJ-2026-0001`)
  - Manufacturer
  - Agreement reference
  - Amount (positive or negative currency string)
  - Type / Reason (e.g., return reversal, manual billing reconciliation)
  - Proposed by & Proposed at
  - Status badge
  - Actions column: For `pending` adjustments, Approvers see buttons `Approve` and `Reject`.

### What proposes adjustments

1. **System recalculation events**:
   - Customer sales returns: Credit memos that reverse an invoice on which rebates were already claimed trigger automatic negative adjustments (e.g., scenario `SC-09r`).
   - Prior-period agreement revisions: Recalculating historical periods with altered tier percentages generates adjustment lines for the difference.
2. **Manual entry**:
   - An Analyst clicks `New adjustment`, selects the Manufacturer and Agreement, enters the exact adjustment amount (positive or negative), chooses the adjustment type, and enters a required explanation.
   - The adjustment is saved in `pending` status.

### Carrying into a batch

- When a new claim batch is created for a manufacturer, Veyra queries all adjustments for that manufacturer in `approved` status.
- The server automatically binds them to the new batch and updates their status from `approved` to `carried`.
- Carried adjustments offset the gross claimable lines: positive adjustments increase the payout; negative adjustments deduct from the batch total.
- Once marked `carried`, an adjustment cannot be edited, deleted, or rejected (`400` `adjustment_already_carried`).

### Negative-claims refusal and manufacturer setting

Some manufacturers refuse claim submissions with a negative net total (where return clawbacks exceed positive sales).
- In **Master data** > **Manufacturers** > `[Manufacturer Detail]`, an Approver or Admin configures the setting:  
  `Allow negative claims` (`frontend/src/features/masterdata/manufacturer-detail-page.tsx:190`).
- If `Allow negative claims` is disabled (default) and the sum of carried adjustments makes the net batch total negative, batch creation or approval is blocked with error `400` `negative_batch_total`.
- When enabled, Veyra permits net-negative batches (which generate a debit memo or credit advice request to the manufacturer).

### Real data example

From `testdata/generated/ground_truth/claim_batches_fixture.json`:
- Adjustment `ADJ-2026-0001`.
- Reason: Customer return reversal for invoice line `CL-2026-000004` (scenario `SC-09r`).
- Amount: **`-50.00`**.
- Status: `carried` into batch `CB-VOLT-2026-06`, reducing the line total of `10,410.00` to net batch payable of `10,360.00`.

### Common errors and backend codes

- **Self-approval blocked**: `403` `four_eyes_violation` ("You proposed this adjustment, so another approver must approve it").
- **Modifying carried adjustment**: `400` `adjustment_already_carried` ("Cannot reject or edit an adjustment that has already been carried into batch {batch_number}").
- **Zero amount**: `400` `zero_amount_adjustment` ("Adjustment amount must be non-zero").
- **Negative batch total rejected**: `400` `negative_batch_total` ("Net batch total cannot be negative for manufacturer {code}").

---

## 13. Remittances and reconciliation

The reconciliation workbench ingests manufacturer payment advices (remittances) in CSV, Excel, or PDF format, parses line items, runs automated matching against submitted claim batches, and identifies short-payments, rejections, and unremitted lines.

### Who may do it

- **Analyst**, **Approver**, **Admin**: Upload remittances, review staged files, run automated matching, confirm/reject matches, execute manual matches, categorise unmatched lines, and finalise reconciliation (`remittance.reconcile`).
- **Viewer**: Read-only access to remittances, matching candidates, and reconciliation findings (`data.read`).

### What the screens show

#### Navigation tabs
Under **Reconciliation** in the sidebar (`frontend/src/features/reconciliation/common.tsx`), three tabs are available:
- `Remittances`: List of all uploaded settlement documents.
- `New remittance`: Upload and intake screen.
- `Reason codes`: Master catalog of dispute and rejection reason codes (e.g. `E07`, `E12`).

#### New remittance upload (`/reconciliation/new`)
On `new-remittance-page.tsx`:
- File drop zone supporting `.csv`, `.xlsx`, and `.pdf` documents up to 50 MB.
- Form inputs:
  - **Manufacturer**: Dropdown selection.
  - **Remittance reference**: Credit memo or payment advice number (e.g. `CM-7781`).
  - **Remittance date**: Date on the payment advice.
  - **Total paid amount**: Stated total payment from the document header.
- Action button: `Upload and check remittance`. Initiates parsing, virus scanning, and structural validation.

#### Remittance detail and staging review (`/reconciliation/:remittanceId`)
Once uploaded, the remittance lands in `staged` status on `remittance-detail-page.tsx`:
- Header card: Displays Reference, Manufacturer, Date, Stated total, and Status (`staged`).
- Staged rows preview: Displays extracted lines from the file.
- Action button: `Accept remittance`. Transitions status from `staged` to `accepted`, enabling the workbench.

#### Reconciliation workbench
Clicking into the accepted remittance opens the interactive workbench:
- Top action bar:
  - `Run matching`: Triggers automated matching algorithms against submitted claim batches.
  - `Finalise`: Closes the reconciliation and generates recovery cases.
- Split workbench comparison grid:
  - **Left pane (Remittance lines)**: Line number, Manufacturer item code, Description, Paid amount, Stated reason code.
  - **Right pane (Claim batch candidates)**: Suggested matching claim line, Batch reference, Claimed amount, Discrepancy amount, and match confidence score.
  - Line-level action buttons:
    - `Confirm match`: Accepts the algorithm suggestion.
    - `Reject match`: Clears the suggestion, returning both lines to the unmatched pool.
    - `Manual match`: Opens a modal to search claim batch lines by SKU, invoice number, or amount and force-bind a match.
    - `Mark as unmatched payment`: Classifies an unexpected manufacturer credit line that does not correspond to any claim batch.

### Reconciliation findings

When reconciliation is evaluated, four distinct finding categories are generated:
1. **Short-paid**: A matched claim line where the manufacturer paid less than claimed (e.g. claimed `2,200.00`, paid `2,050.00`, short-paid by `150.00`). A dispute reason code is assigned (e.g. `E12` / `rejected_price_mismatch`).
2. **Rejected**: A claim line explicitly rejected by the manufacturer with zero payment (e.g. claimed `2,080.00`, paid `0.00`, rejected amount `2,080.00`). Assigned a reason code (e.g. `E07` / `rejected_quantity_exceeds_authorization`).
3. **Unmatched payment**: Remittance payment line with no corresponding claim batch line (e.g. manufacturer discretionary incentive or retrospective rebate).
4. **Unpaid after payment lag**: Claim lines in submitted batches where the manufacturer's contractual payment lag (e.g. 30 or 45 days) has elapsed without the line appearing on any remittance. The system marks them as `unpaid` (`unpaid_no_remittance`).

### Finalisation

- When all lines are reconciled, click `Finalise`.
- The modal `Confirm finalisation` appears, detailing total lines, confirmed matches, and findings.
- Clicking confirm locks the remittance (`finalised`). The server automatically creates recovery cases in the **Recovery queue** for all short-paid, rejected, and unpaid findings.

### Real data example

From `testdata/generated/ground_truth/expected_reconciliation.json`:
- Remittance reference: Credit memo `CM-7781` from Voltara (`VOLT`).
- Total paid amount: **`7,545.00`**. Remittance date: `2026-07-31`.
- Matched against batch `CB-VOLT-2026-06` (claim total `10,360.00`):
  - **Line 1 (`CL-2026-000001`)**: Claimed `2,200.00`, paid `2,050.00` -> **Short-paid by `150.00`** with reason code `E12`.
  - **Line 3 (`CL-2026-000003`)**: Claimed `2,080.00`, paid `0.00` -> **Rejected by `2,080.00`** with reason code `E07`.
  - **Line 6**: Remittance credit payment of **`75.00`** with no batch line -> **Unmatched payment `75.00`**.
  - **Line 7 (`CL-2026-000007`)**: Claimed for `660.00`, omitted from remittance -> **Unpaid after payment lag `660.00`**.
- Deadlines: 60-day dispute window from remittance date `2026-07-31` establishes statutory dispute deadline of **`2026-09-29`**, with reminder thresholds at 14 days, 7 days, and 3 days.

### Common errors and backend codes

- **Unsupported file format**: `400` `unsupported_remittance_format` ("File format not supported. Only CSV, XLSX, and PDF are accepted").
- **Header amount mismatch**: `422` `remittance_total_mismatch` ("Staged line sum does not match remittance header total").
- **Modifying finalised remittance**: `409` `remittance_already_finalised` ("Cannot alter matches on a finalised remittance").
- **Unresolved lines remaining**: `400` `unresolved_lines_exist` ("All remittance lines must be matched or marked as unmatched before finalising").

---

## 14. Recovery queue and case work

The recovery queue manages actionable dispute findings resulting from reconciliation. Analysts investigate discrepancies, submit reclaims, or propose write-offs under four-eyes control.

### Who may do it

- **Analyst**: View queue, assign cases, update status, add investigation notes and attachments, execute reclaims, and propose write-offs (`case.work`).
- **Approver**, **Admin**: All analyst actions, plus approve proposed write-offs (`case.write_off`).
- **Four-eyes rule for write-off**: An analyst or approver who proposes a write-off cannot approve it. A second approver or admin must review and approve (`403` `four_eyes_violation`).
- **Viewer**: Read-only access to recovery queue, case details, notes, and timelines (`data.read`).

### What the screens show

#### Recovery queue (`/recovery`)
Navigate to **Recovery** in the sidebar (`recovery-queue-page.tsx`).
- Subnavigation tabs: `Recovery queue` and `Notifications` (`frontend/src/features/recovery/common.tsx`).
- Summary strip (`Recovery summary`):
  - Total open cases
  - Total at risk ($)
  - Overdue cases
  - Cases expiring within 7 days
- Urgency ordering:
  - The queue is sorted by deadline urgency.
  - Cases marked `Unknown deadline` (where manufacturer contract terms lack explicit dispute window rules) are pinned to the top of the queue for immediate triaging.
- Queue table columns: Case ID, Finding type badge (`short_paid`, `rejected`, `unpaid`), Manufacturer, Claim reference, Amount at risk, Dispute deadline, Basis & reminder tier badge, Status (`open`, `in_progress`, `reclaimed`, `written_off`), Owner.

#### Recovery case detail (`/recovery/:caseId`)
Opening a case reveals `recovery-case-page.tsx`:
- **Header**: Case ID, Manufacturer, Status badge, Amount at risk, and Dispute deadline with legal/contractual basis.
- **Dispute assessment card**: Summarises evidence from original calculation run, agreement terms, claimed amount, remittance paid amount, discrepancy, and manufacturer reason code.
- **Action bar**:
  - `Assign`: Reassigns case ownership. Modal confirms with button `Save assignment`.
  - `Change status`: Updates progress (`open`, `in_progress`, etc.). Modal confirms with button `Save status`.
  - `Reclaim`: Flags the case as recovered upon receiving an amended credit memo or correction.
  - `Write off`: Opens the write-off dialog.
- **Case history and notes**: Audit timeline of all state transitions, notes, and uploaded evidence files.

### Write-off four-eyes workflow

Write-offs permanently forfeit recovery of an outstanding balance. Because write-offs directly impact gross margin, Veyra strictly enforces four-eyes separation between proposal and approval.

1. **Proposing a write-off**:
   - Any Analyst, Approver, or Admin clicks `Write off` on an open case.
   - If no proposal exists, the dialog opens with a mandatory justification textarea:
     - Label: **Write-off reason** (maximum 1,000 characters; control characters/NUL rejected).
     - Action button: `Propose write-off` (or `Complete write-off` if an approver initiates).
   - Once submitted, the case stores `write_off_proposed_by`, `write_off_proposed_at`, and the verbatim `write_off_reason`. The status transitions to `write_off_proposed`.

2. **Proposer view**:
   - If the user who proposed the write-off opens the dialog, the input is disabled and displays:  
     `You proposed this write-off. A second approver must approve it.`  
     The confirmation button is disabled.

3. **Approver view**:
   - When a distinct Approver or Admin opens the case and clicks `Write off`:
     - The dialog presents the proposer's explanation in a read-only container:  
       `Reason proposed by <proposer_name> on <date>`.
     - The action button is labelled `Approve write-off`.
     - The approver cannot edit the text. The backend strictly retains the proposer's stored reason and records the approver's user ID and timestamp.

### Real data example

From `testdata/generated/ground_truth/expected_reconciliation.json`:
- Recovery case for Voltara short-payment on line `CL-2026-000001`:
  - Amount at risk: **`150.00`** (claimed `2,200.00`, paid `2,050.00`).
  - Reason: `E12` (`rejected_price_mismatch`).
  - Remittance date: `2026-07-31`. Dispute window: 60 days -> Deadline: **`2026-09-29`**.
  - Escalation tiers: Reminders generated at 14 days, 7 days, and 3 days before deadline.
- Recovery case for Voltara rejection on line `CL-2026-000003`:
  - Amount at risk: **`2,080.00`** (claimed `2,080.00`, paid `0.00`).
  - Reason: `E07` (`rejected_quantity_exceeds_authorization`).

### Common errors and backend codes

- **Four-eyes write-off violation**: `403` `four_eyes_violation` ("You proposed this write-off, so a second approver must approve it").
- **Missing write-off reason**: `400` `write_off_reason_required` ("A write-off reason is required (up to 1,000 characters)").
- **Invalid case state transition**: `400` `invalid_case_transition` ("Cannot transition case from '{current_status}' to '{target_status}'").
- **Case not found**: `404` `case_not_found` ("Recovery case '{case_id}' does not exist").

---

## 15. Notifications

The notifications center alerts users to critical lifecycle events, upcoming dispute deadlines, and required four-eyes approvals.

### Who may do it

All authenticated roles (**Analyst**, **Approver**, **Admin**, **Viewer**) receive notifications tailored to their permissions and assigned cases.

### What the screen shows

Navigate to **Recovery** > `Notifications` tab (`frontend/src/features/recovery/notifications-page.tsx`).
- Filter control: Checkbox `Unread only` toggles between viewing unread alerts and the full notification archive.
- Action button: `Mark read` on individual items, or mark all as read.
- Notification card components:
  - Event title and description (e.g., "Dispute deadline approaching: 7 days remaining for case RC-VOLT-0012", "Claim batch CB-VOLT-2026-06 requires approval", "Write-off proposed for case RC-VOLT-0003").
  - Relative and absolute timestamp.
  - Interactive deep links: Clicking the card or link navigates directly to the associated entity:
    - `/recovery/:caseId` for recovery cases
    - `/claims/batches/:batchId` for claim batches
    - `/reconciliation/:remittanceId` for remittances

---

## 16. Dashboard, drill-down, trace and audit pack

Executive reporting provides real-time financial visibility into earned rebates, recovered amounts, outstanding disputes, and complete end-to-end audit lineage.

### Who may do it

- **Dashboard and Drill-down viewing**: Analyst, Approver, Admin, Viewer (`report.read`).
- **Refresh figures**: Analyst, Approver, Admin (`report.refresh`).
- **Trace chain viewing**: Analyst, Approver, Admin, Viewer (`data.read`).
- **Audit pack export**: Analyst, Approver, Admin, Viewer (`audit_pack.export`).

### What the screens show

#### Executive Dashboard (`/reporting`)
On `dashboard-page.tsx`:
- Header title: `Executive Dashboard`.
- Action button: `Refresh figures` (triggers background recalculation of reporting rollups).
- Top KPI summary cards:
  - **Total Recovered**: Total monetary recovery achieved through reclaims and successful dispute resolutions.
  - **Open At Risk**: Total exposure across open and in-progress recovery cases.
  - **Claimed Batches**: Total value submitted across active claim batches.
  - **Reconciled**: Cumulative payments received and matched via remittances.
  - **Loss / Written Off**: Cumulative total written off under four-eyes approvals.
- Visualization panels:
  - `Monthly breakdown`: Interactive trend chart and monthly table displaying Gross Claimed, Remitted, Disputed, and Written Off figures month by month.

#### Drill-down (`/reporting/drilldown`)
Every summary metric and chart segment on the dashboard is clickable. Clicking a figure navigates to the Drill-down page:
- Displays the exact line-item rows that constitute the aggregated total.
- Columns: Transaction date, Entity reference, Manufacturer, Product SKU, Customer, Line amount, and Status.
- Direct trace action: Each row includes a link button: `Trace →`.

#### Trace chain (`/reporting/trace/:entityType/:entityId`)
On `trace-page.tsx`:
- Header: `Trace chain`.
- Renders the complete immutable audit trail of a transaction from inception to final accounting:
  1. **Source Transaction**: Original ERP sales or purchase line (invoice number, date, SKU, customer).
  2. **Calculation Run**: Agreement rule evaluated, calculation engine run ID, rate applied, and computed rebate amount.
  3. **Claim Batch**: Batch number, export template used, approver, and submission date.
  4. **Remittance**: Matched payment advice reference, paid amount, and discrepancy reason code.
  5. **Recovery Case**: Case history, assigned owner, dispute notes, and resolution (reclaim or approved write-off).
  6. **Cryptographic Audit Seal**: Cryptographic SHA-256 seal anchoring the record in the append-only audit log.

#### Audit pack generation (`/reporting/audit-pack`)
On `audit-pack-page.tsx`:
- Configuration options: Select Manufacturer, Fiscal period range, and inclusion options (calculation logs, batch manifests, remittance matches, dispute logs).
- Action button: `Generate audit pack (XLSX)`.
- Background processing: Triggers a `POST /api/v1/reporting/audit-pack`. The user interface displays a progress bar and polls the background job until it reaches `succeeded`.
- Download: Once complete, the button transitions to `Download audit pack (XLSX)`. The resulting workbook provides an auditor-ready file containing timestamped sheets, calculation proofs, four-eyes approval logs, and cryptographic seal verification hashes.

### Real data example

From `claim_batches_fixture.json` and `expected_reconciliation.json`:
- June 2026 Voltara reconciliation cycle:
  - Gross batch claimed: **`10,360.00`** (Batch `CB-VOLT-2026-06`).
  - Total remitted & reconciled: **`7,545.00`** (Credit memo `CM-7781`).
  - Open at risk in recovery: **`2,890.00`** (Line 1 short-paid `150.00`, Line 3 rejected `2,080.00`, Line 7 unpaid `660.00`).
  - Unmatched manufacturer payment credit: **`75.00`**.

### Common errors and backend codes

- **Invalid date range**: `400` `invalid_date_range` ("End date must be on or after start date").
- **Generation timeout**: `504` `report_timeout` ("Report generation timed out. Please narrow your date range or filter by manufacturer").
- **Trace record missing**: `404` `entity_trace_not_found` ("Trace chain cannot be assembled for the requested entity").
