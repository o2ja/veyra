# Complete Testing Walkthrough

This guide provides a comprehensive, click-by-click walkthrough for testing the Veyra Recover application locally. It is written so that a non-developer can verify the entire recovery lifecycle—from user registration and master data ingestion through calculations, claims, remittances, recovery cases, write-offs, and executive reporting.

Every action, label, button, and expected value in this guide is derived directly from the automated Playwright specifications in `frontend/e2e/` and the synthetic fixtures in `testdata/generated/`.

---

## Prerequisites & Local Environment

1. **Local Services Running**:
   - Web Application: `http://localhost:5173`
   - Local Mail Inbox (Mailpit): `http://localhost:8025`
   - Synthetic Test Files: located in `testdata/generated/`
2. **Two Browser Sessions Needed**:
   - **Browser A** (Primary): Standard browser window for the **Admin** user.
   - **Browser B** (Secondary): Private/incognito window or distinct browser profile for the **Approver** user (required to test four-eyes separation of duties).
3. **Authenticator App / TOTP Tool**:
   - An authenticator application (Google Authenticator, 1Password, or any RFC 6238 TOTP tool) to generate 6-digit MFA codes from base32 setup keys.
4. **Standard Test Password**:
   - `correct horse battery staple 42` (used across all Playwright specs).

---

## Walkthrough Roadmap

- **Phase 1**: Registration, Email Verification & MFA Enrollment (`support.ts`, `w1-w2.spec.ts`)
- **Phase 2**: Multi-User Setup & Approver Invitation (`w1-w2.spec.ts`, `claims.spec.ts`)
- **Phase 3**: Master Data Ingestion in Dependency Order (`masterdata.spec.ts`, `imports.spec.ts`, `claims.spec.ts`)
- **Phase 4**: SPA Agreement Draft Creation (`agreements.spec.ts`, `claims.spec.ts`)
- **Phase 5**: Four-Eyes Agreement Approval (`agreements.spec.ts`, `claims.spec.ts`)
- **Phase 6**: Rebate Calculation Run (`calculations.spec.ts`, `claims.spec.ts`)
- **Phase 7**: Missed Claims Discovery & Batch Creation (`claims.spec.ts`)
- **Phase 8**: Four-Eyes Batch Approval, Export & Submission (`claims.spec.ts`)
- **Phase 9**: Remittance Reason Codes, Ingestion & Workbench Reconciliation (`reconciliation-workbench.spec.ts`)
- **Phase 10**: Recovery Queue, Case Investigation & Four-Eyes Write-Off (`recovery-queue.spec.ts`, `recovery-case.spec.ts`, `recovery-writeoff.spec.ts`)
- **Phase 11**: Executive Dashboard, Drill-Down, Trace Chain & Audit Pack (`reporting-dashboard.spec.ts`)

---

## Phase 1: Registration, Email Verification & MFA Enrollment

*Derived from: `frontend/e2e/support.ts` (`registerWorkspace`, `enrolMfa`), `frontend/e2e/w1-w2.spec.ts`.*

### Step-by-Step Actions

1. In **Browser A**, navigate to `http://localhost:5173/register`.
2. Fill in the registration form:
   - **Your name**: `Claims Admin`
   - **Company name**: `Voltara Claims Co (SYNTHETIC)`
   - **Work email**: `admin@example.test` (or any unique email)
   - **Password**: `correct horse battery staple 42`
3. Click the button: **`Create workspace`**.
4. Open your browser to the Mailpit inbox at `http://localhost:8025`.
5. Open the newest email sent to `admin@example.test` with subject containing workspace verification. Click the confirmation link or copy the token from the link `#token=<TOKEN>`.
6. Return to `http://localhost:5173/verify-email#token=<TOKEN>`:
   - **Password**: `correct horse battery staple 42`
   - Click the button: **`Confirm and create workspace`**.
7. You are redirected to `/login?reason=verified`. Log in:
   - **Email**: `admin@example.test`
   - **Password**: `correct horse battery staple 42`
   - Click the button: **`Sign in`**.
8. Because MFA is mandatory for the workspace, the browser lands on `/mfa/setup`:
   - Click the button: **`Start setup`**.
   - An authenticator QR code appears. Click the button: **`Can't scan? Show the setup key`**.
   - Copy the base32 secret displayed inside the `<code>` element into your authenticator app.
   - Enter the current 6-digit code from your authenticator into the field: **`6-digit code`**.
   - Click the button: **`Turn on two-factor authentication`**.
   - A list of 10 one-time recovery codes is displayed. Click the button: **`I saved them, continue`**.

### What You Should SEE

- The browser lands on the workspace home screen (`/`).
- Top navigation displays the workspace title: **`Voltara Claims Co (SYNTHETIC)`**.
- User profile shows your name: **`Claims Admin`** with role badge **`admin`**.

> **If it looks different**: If clicking "Sign in" before email verification displays `"The email or password is incorrect."`, verify that the token link was clicked in Mailpit. If MFA setup rejects the 6-digit code, ensure your computer clock is synchronized to UTC.

---

## Phase 2: Multi-User Setup & Approver Invitation

*Derived from: `frontend/e2e/support.ts` (`inviteMember`), `frontend/e2e/claims.spec.ts:104-120`.*

### Step-by-Step Actions

1. In **Browser A** (Admin session), click **Invitations** in the navigation menu (or navigate to `/admin/invitations`).
2. Fill in the invitation form:
   - **Email**: `approver@example.test`
   - **Role**: select `approver` from the dropdown
   - Click the button: **`Invite`**.
   - Verify the alert banner appears: `"Invitation created for approver@example.test"`.
3. Open the Mailpit inbox at `http://localhost:8025`. Open the invitation email sent to `approver@example.test` and copy the link: `/accept-invite#token=<TOKEN>`.
4. Open **Browser B** (Incognito / Private Window) and navigate to `http://localhost:5173/accept-invite#token=<TOKEN>`.
5. Complete the acceptance form in **Browser B**:
   - **Your name**: `Claims Approver`
   - **Password**: `correct horse battery staple 42`
   - Click the button: **`Accept invitation`**.
6. Redirects to `/login?reason=invited`. Sign in:
   - **Email**: `approver@example.test`
   - **Password**: `correct horse battery staple 42`
   - Click the button: **`Sign in`**.
7. Complete MFA setup for the Approver in **Browser B**:
   - Click **`Start setup`** -> Click **`Can't scan? Show the setup key`**.
   - Add this second key as a distinct entry in your authenticator app (labeled "Approver").
   - Enter the 6-digit code -> Click **`Turn on two-factor authentication`** -> Click **`I saved them, continue`**.

### What You Should SEE

- In **Browser B**, user profile shows **`Claims Approver`** with role **`approver`**.
- Navigation items restricted to admins (such as `Users`) are absent from Browser B's menu.

---

## Phase 3: Master Data Ingestion in Dependency Order

*Derived from: `frontend/e2e/masterdata.spec.ts`, `frontend/e2e/imports.spec.ts`, `frontend/e2e/claims.spec.ts:78-103`.*

Master data must be ingested in strict relational order so foreign key references resolve properly.

### Step-by-Step Actions (in Browser A as Admin)

#### 1. Add Manufacturers
1. Navigate to **Master data** in the sidebar (or `/master-data/manufacturers`).
2. Add the four manufacturers from `testdata/generated/ground_truth/master_manufacturers.json`:
   - Name: `Voltara Electric (SYNTHETIC)` | Code: `VOLT` -> click **`Add manufacturer`**.
   - Name: `Brightline Lighting (SYNTHETIC)` | Code: `BRIT` -> click **`Add manufacturer`**.
   - Name: `Coppermark Wire & Cable (SYNTHETIC)` | Code: `COPR` -> click **`Add manufacturer`**.
   - Name: `Northwind HVAC (SYNTHETIC)` | Code: `NWND` -> click **`Add manufacturer`**.
3. Verify all four rows appear in the table **`Manufacturers`**.

#### 2. Add Branch
1. Navigate to `/master-data/branches` (via Master data subnavigation).
2. Enter:
   - **Name**: `Branch 01 (SYNTHETIC)`
   - **Code**: `BR01`
3. Click the button: **`Add branch`**. Notice status banner: `"Branch added."`.

#### 3. Upload Master Data CSV Files
Navigate to `/imports/new` for each of the following files in `testdata/generated/` in exact order:

1. **Customers**:
   - File: `testdata/generated/master/customers.csv`
   - **What the file contains**: select `Customers`
   - Click **`Start import`** -> Click **`Apply mapping and check rows`** -> Click **`Confirm import`**.
2. **Products**:
   - File: `testdata/generated/master/products.csv`
   - **What the file contains**: select `Products`
   - Click **`Start import`** -> Click **`Apply mapping and check rows`** -> Click **`Confirm import`**.
3. **UoM Conversions**:
   - File: `testdata/generated/master/uom_conversions.csv`
   - **What the file contains**: select `UoM conversions`
   - Click **`Start import`** -> Click **`Apply mapping and check rows`** -> Click **`Confirm import`**.
4. **Sales Transactions**:
   - File: `testdata/generated/sales/sales_scenarios_layoutA.csv`
   - **What the file contains**: select `Sales`
   - Click **`Start import`** -> Click **`Apply mapping and check rows`** -> Click **`Confirm import`**.

### What You Should SEE

- After each import, the browser navigates to `/imports/:id` showing heading **`Result`**.
- Status badge shows: **`Completed`**.
- Row summary cards show: valid rows processed with `0` row errors.

> **If it looks different**: If an import halts on "Map the columns", click `Apply mapping and check rows` to continue. All header names in `layoutA` match standard aliases automatically.

---

## Phase 4: SPA Agreement Draft Creation

*Derived from: `frontend/e2e/agreements.spec.ts`, `frontend/e2e/claims.spec.ts:121-159`, `testdata/generated/agreements/manual_payloads/SPA-VOLT-2026-884.json`.*

### Step-by-Step Actions (in Browser A as Admin)

1. Navigate to **Agreements** > **Agreement drafts** (`/agreement-drafts`).
2. Click the button: **`New draft`**.
3. In the agreement form, enter the header fields:
   - **Agreement type**: select `spa`
   - **External number**: `SPA-VOLT-2026-884`
   - **Manufacturer code**: `VOLT`
   - **Name**: `Voltara contractor SPA (SYNTHETIC)`
   - **Valid from**: `2026-01-01`
   - **Valid to**: `2026-12-31`
4. Add Customer Scope:
   - Click **`Add customer`** three times.
   - Enter Customer codes:
     - Row 1: `ALDER`
     - Row 2: `BLUFF`
     - Row 3: `CEDAR`
5. Add Item Scope:
   - Click **`Add item`** three times.
   - Enter exact SKU pricing:
     - **Item 1**:
       - SKU: `BRK-3P-100A`
       - Reference cost: `100.00`
       - Deviated cost: `78.00`
       - Sell price: `88.00`
     - **Item 2**:
       - SKU: `BRK-3P-60A`
       - Reference cost: `64.50`
       - Deviated cost: `52.00`
       - Sell price: `58.00`
     - **Item 3**:
       - SKU: `PNL-200A`
       - Reference cost: `412.00`
       - Deviated cost: `360.00`
       - Sell price: `389.00`
       - Max quantity: `100`
6. Click the button: **`Save draft`**. Notice notification: `"Draft saved."`.
7. In the section **Validation**, click the button: **`Validate`**.
   - Notice status: `"Validation passed."`.
8. Click the button: **`Submit for approval`**.
   - Notice status: `"Draft submitted for approval."`.
9. Copy the draft URL (e.g. `http://localhost:5173/agreement-drafts/<DRAFT-UUID>`).

### What You Should SEE

- Draft status badge displays: **`Submitted`**.
- Action buttons in Browser A show that the creator cannot approve their own draft (Four-eyes rule).

---

## Phase 5: Four-Eyes Agreement Approval

*Derived from: `frontend/e2e/claims.spec.ts:160-164`, `frontend/e2e/reconciliation-workbench.spec.ts:163-167`.*

### Step-by-Step Actions (in Browser B as Approver)

1. In **Browser B**, paste the draft URL `/agreement-drafts/<DRAFT-UUID>` (or go to `/agreement-drafts` and click the submitted draft).
2. Verify the draft details: External number `SPA-VOLT-2026-884`, Manufacturer `VOLT`, 3 items.
3. Click the button: **`Approve draft`**.

### What You Should SEE

- Success banner displays: **`Draft approved.`**.
- Status transitions to: **`Approved`**.
- An immutable version is generated and now active for calculations.

---

## Phase 6: Rebate Calculation Run

*Derived from: `frontend/e2e/calculations.spec.ts`, `frontend/e2e/claims.spec.ts:165-175`, `testdata/generated/ground_truth/expected_calculation.json`.*

### Step-by-Step Actions (in Browser A as Admin)

1. In **Browser A**, navigate to **Calculations** in the sidebar (or `/calculations`).
2. In the calculation run trigger form:
   - **Date from**: `2026-01-01`
   - **Date to**: `2026-12-31`
3. Click the button: **`Start calculation run`**.
4. The table **`Calculation runs`** displays the queued run row.
5. Wait for the background worker to finish processing (typically 5 to 15 seconds).

### What You Should SEE

- The run row updates from `Queued` -> `Running` -> **`Completed`**.
- Clicking into the completed run shows eligible lines computed with exact decimal precision, including:
  - Line `INV-10060` Line 1 (`PNL-200A`): 30 units × 22.00 price difference = **`660.00`** rebate entitlement (Scenario `SC-22`).
  - Lines for invoices `INV-10001` through `INV-10006` calculated as eligible.

> **If it looks different**: If the run stays "Queued" for more than 30 seconds, ensure the Docker worker service is healthy (`docker compose ps worker`).

---

## Phase 7: Missed Claims Discovery & Batch Creation

*Derived from: `frontend/e2e/claims.spec.ts:176-209`, `testdata/generated/ground_truth/claim_batches_fixture.json`.*

### Step-by-Step Actions (in Browser A as Admin)

1. In **Browser A**, navigate to **Claims** in the sidebar. You land on `/claims/missed`.
2. In the **`Missed claims filters`** bar:
   - **Manufacturer**: select `Voltara Electric (SYNTHETIC) (VOLT)`
3. Click the button: **`Apply filters`**.
4. The table **`Missed claim lines`** renders unbatched eligible lines.
5. In the table, check the selection checkbox on the first claim line row (or multiple rows).
6. Click the button above the table: **`Create batch from selected`** (or **`Create batch from all matching filter`**).
7. A confirmation dialog **`Create batch`** opens. Click the button: **`Create batch`**.

### What You Should SEE

- The browser automatically navigates to `/claims/batches/<BATCH-UUID>`.
- Batch status badge displays: **`Draft`**.
- Notice the four-eyes alert banner:  
  **`Four-eyes rule: you created this batch, so a second approver must approve it.`**
- The button **`Approve batch`** is disabled or hidden for Admin in Browser A.
- Summary card shows: **`Batch total (lines + carried adjustments, from server)`**.

---

## Phase 8: Four-Eyes Batch Approval, Export & Submission

*Derived from: `frontend/e2e/claims.spec.ts:210-278`.*

### Step-by-Step Actions

#### 1. Approver Approves Batch (in Browser B)
1. In **Browser B** (Approver), navigate to **Claims** > **Claim batches** (`/claims/batches`).
2. Click into the new `Draft` batch.
3. Because Approver did not create the batch, the button **`Approve batch`** is enabled.
4. Click **`Approve batch`** -> In the confirmation dialog, click **`Approve batch`**.
5. Batch status badge updates to **`Approved`**.

#### 2. Create Export Template (in Browser A)
1. In **Browser A**, navigate to **Claims** > **Export templates** (`/claims/templates`).
2. Click the button: **`New template`**.
3. Fill in the template form:
   - **Template name**: `VOLT Test Template`
   - **Manufacturer**: select `Voltara Electric (SYNTHETIC) (VOLT)`
4. Click the button: **`Save template`**. The template appears in the table.

#### 3. Export Batch (in Browser A)
1. Return to the batch detail page `/claims/batches/<BATCH-UUID>`. Status is `Approved`.
2. Click the button: **`Export batch`**.
3. In the dialog:
   - **Export template**: select `VOLT Test Template`
   - Click the button: **`Start export`**.
4. Wait for the background job to finish; the button updates to: **`Download exported claim`** (or `Download generated export`).

#### 4. Submit Batch (in Browser A)
1. On the same batch page, click the button: **`Submit batch`**.
2. In the modal dialog **`Confirm submission`**:
   - **External reference**: enter `VOLT-BATCH-01`
   - **Submission date**: today's date
3. Click the button: **`Confirm submission`**.

### What You Should SEE

- Batch status updates from `Approved` to **`Submitted`**.
- An indicator badge displays: **`Frozen`**.
- All lines are permanently locked against re-batching.
- Returning to `/claims/missed` shows that the submitted claim lines have disappeared from the unbatched pool.

---

## Phase 9: Remittance Reason Codes, Ingestion & Workbench Reconciliation

*Derived from: `frontend/e2e/reconciliation-workbench.spec.ts:216-435`, `testdata/generated/ground_truth/expected_reconciliation.json`.*

### Step-by-Step Actions (in Browser A as Admin)

#### 1. Configure Reason Code Mappings
1. Navigate to **Reconciliation** > **Reason codes** (`/reconciliation/reason-codes`).
2. **Manufacturer**: select `Voltara Electric (SYNTHETIC) (VOLT)`.
3. Click **`Add mapping`** twice and configure:
   - **Row 1**:
     - External code: `E12`
     - External description: `Price discrepancy`
     - Controlled reason code: select `rejected_price_mismatch`
   - **Row 2**:
     - External code: `E07`
     - External description: `Quantity exceeds cap`
     - Controlled reason code: select `rejected_quantity_exceeds_authorization`
4. Click the button: **`Save mappings`**. Notice banner: `"Reason code mappings saved successfully."`.

#### 2. Upload and Ingest Remittance
1. Navigate to **Reconciliation** > **New remittance** (`/reconciliation/new`).
2. Fill in the intake form:
   - **Remittance file**: select `testdata/generated/remittance/credit_memo_VOLT_CM-7781.csv`
   - **Manufacturer**: select `Voltara Electric (SYNTHETIC) (VOLT)`
   - **Document type**: select `credit_memo`
   - **Channel**: select `credit_memo`
   - **Document number**: `CM-7781`
3. Click the button: **`Create and ingest remittance`** (or `Upload and check remittance`).
4. The background parser finishes, and the browser navigates to `/reconciliation/<REMITTANCE-UUID>`.

#### 3. Staging Review and Acceptance
1. On the staged remittance page:
   - **Document total**: enter `7545.00` (ground truth credit memo total from `expected_reconciliation.json`).
2. Click the button: **`Accept remittance`**.
3. In the confirmation dialog, click **`Accept`**.

#### 4. Interactive Workbench Matching
1. The screen displays the heading: **`Reconciliation workbench`**.
2. Click the button: **`Run matching`**.
3. Automated algorithms match lines:
   - Lines 1–4 are automatically matched against submitted batch items.
   - For **Remittance line 5**, a subset-sum match suggestion appears. Click the button: **`Confirm`**.
   - For **Remittance line 6** (amount `75.00` for `INV-99999` with no matching batch claim line): click the button **`Mark as unmatched payment`**.
   - Notice Line 6 status updates to: `"Categorised: unmatched_payment"`.

#### 5. Finalise Reconciliation
1. Click the button: **`Finalise`**.
2. The modal **`Finalise reconciliation`** opens, showing confirmed line counts and findings.
3. Click the button: **`Confirm finalisation`**.

### What You Should SEE

- Banner displays:  
  **`This remittance has been finalised and reconciled. Matches and allocations are immutable.`**
- Heading appears: **`Reconciliation findings (3)`**.
- Findings table lists the exact discrepancies:
  - **Line 1**: Short-paid by **`150.00`** (claimed `2,200.00`, paid `2,050.00`, reason code `E12`).
  - **Line 3**: Rejected by **`2,080.00`** (claimed `2,080.00`, paid `0.00`, reason code `E07`).
  - **Line 6**: Unmatched payment of **`75.00`**.

> **If it looks different**: If clicking "Finalise" displays an error alert `"Match or categorise every remittance line before finalising"`, check that Line 6 was clicked with `Mark as unmatched payment`.

---

## Phase 10: Recovery Queue, Case Investigation & Four-Eyes Write-Off

*Derived from: `frontend/e2e/recovery-queue.spec.ts`, `frontend/e2e/recovery-case.spec.ts`, `frontend/e2e/recovery-writeoff.spec.ts`.*

### Step-by-Step Actions

#### 1. Inspect Recovery Queue (in Browser A)
1. Navigate to **Recovery** in the sidebar (`/recovery`).
2. Notice the summary strip: **`Recovery summary`** showing open cases and total amount at risk.
3. Table **`Recovery cases`** displays the generated findings. Notice that the case with **`Unknown deadline`** (Line 6 unmatched payment of `75.00`) is pinned to the top of the queue.

#### 2. Case Investigation & Notes (in Browser A)
1. Click into the **Short-paid** case (amount at risk `150.00`, Line 1 `CL-2026-000001`).
2. **Assign Case**:
   - Click the button: **`Assign`**.
   - In the modal dialog, select `Assign to me` from the Owner dropdown.
   - Click the button: **`Save assignment`**. Case owner updates to `"You"`.
3. **Add Note**:
   - In the **Note** textarea, type: `Contacted Voltara credit department regarding price discrepancy on Line 1.`
   - Click the button: **`Add note`**. The note immediately appears in the **`Timeline`**.
4. **Upload Attachment**:
   - In **Attachment file**, select `testdata/generated/remittance/SYNTHETIC_credit_memo_VOLT_CM-7781.pdf`.
   - Click the button: **`Upload attachment`**. The attachment appears in the **`Attachments`** list.

#### 3. Propose Write-Off (in Browser A as Proposer)
1. On the same short-paid case (`150.00`), click the button: **`Write off`**.
2. In the write-off modal dialog:
   - **Reason**: enter `Manufacturer will not accept the claim`
   - Click the button: **`Write off`** (or `Propose write-off`).
3. Notice status alert: `"Write-off proposed; a second approver must complete it."`.
4. The button updates to **`Complete write-off`** and is **disabled** for Browser A. A status card indicates: `"You proposed this write-off on <DATE>"`.

#### 4. Approve Write-Off (in Browser B as Second Approver)
1. Switch to **Browser B** (Approver).
2. Navigate to `/recovery` and click into the same short-paid case.
3. The button **`Approve write-off`** is enabled. Click **`Approve write-off`**.
4. The dialog displays the proposer's reason in read-only format:  
   `"Reason proposed by Claims Admin: Manufacturer will not accept the claim"`.
5. Click the button: **`Approve write-off`**.

### What You Should SEE

- Notification banner displays: **`Case written off.`**.
- Case status badge updates to: **`Written off`**.
- Metric field **`Written off amount`** displays: **`150.00`** (matching ground truth `expected_reconciliation.json`).

---

## Phase 11: Executive Dashboard, Drill-Down, Trace Chain & Audit Pack

*Derived from: `frontend/e2e/reporting-dashboard.spec.ts:216-297`.*

### Step-by-Step Actions (in Browser A as Admin)

#### 1. Executive Dashboard & Refresh
1. Navigate to **Dashboard** (`/dashboard` or `/reporting`).
2. Click the button: **`Build figures`** (or **`Refresh figures`**).
3. Status message appears: `"Dashboard refresh queued."`.
4. Wait a few seconds for rollups to calculate. Region **`Key performance figures`** populates.
5. Notice the **`Claimed`** card displays the exact submitted batch figure (e.g. `660.0000` or `10360.0000`).
6. Table **`Monthly breakdown table`** displays monthly aggregated columns.

#### 2. Drill-Down Verification
1. Click the **`Claimed`** KPI card (or click a monthly figure link).
2. The browser navigates to `/dashboard/claimed` (heading: **`Drill-down: Claimed`**).
3. The metric banner confirms:
   - Metric figure matches the claimed batch total.
   - Table **`Claimed drill-down items`** lists each underlying line item. The sum of the line item amounts equals the top metric figure exactly.

#### 3. Trace Chain Audit Trail
1. In the drill-down table, click the link on the first line: **`Trace →`**.
2. The browser navigates to `/trace/claim_line/<ID>` (heading: **`Trace chain`**).
3. The timeline shows the full cryptographic and business lineage:
   - Source Sales Document line (invoice, date, SKU, customer).
   - Calculation Run & rule evaluation.
   - Claim Batch reference and submission timestamp.
   - Remittance credit memo match and discrepancy code.
   - Recovery case resolution and audit seal stamp.

#### 4. Audit Pack Export
1. Navigate to **Reports** > **Audit pack** (`/reports/audit-pack`).
2. Form options:
   - **Manufacturer**: select `Voltara Electric (SYNTHETIC) (VOLT)`
   - **Submitted claim batch**: select the submitted batch (`VOLT-BATCH-01`)
3. Click the button: **`Generate audit pack (XLSX)`**.
4. The region **`Export status`** shows progress polling until status is **`succeeded`**.
5. Click the button: **`Download audit pack (XLSX)`** to receive the auditor-ready spreadsheet workbook.

---

## What This Walkthrough Does Not Cover

To maintain a focused end-to-end recovery path, this walkthrough does not cover the following application screens:

1. **Authentication Utilities**:
   - `/forgot-password` and `/reset-password` (self-service password reset via email).
   - `/confirm-email` (email address modification confirmation).
   - `/account/security` (password changes and regenerating MFA recovery codes).
2. **Administration & Queue Debugging**:
   - `/admin/users` (user status and role assignment table).
   - `/admin/settings` (workspace timezone, MFA enforcement, and retention settings).
   - `/admin/audit` (raw system-wide audit event viewer).
   - `/admin/jobs` (background queue manager and dead letters).
3. **Master Data & Files**:
   - `/files` and `/files/:id` (standalone raw file browser, beyond the import/remittance dropzones).
   - `/master-data/customers/:id`, `/master-data/products/:id`, `/master-data/manufacturers/:id` (single entity edit screens).
   - `/master-data/unmapped` (unmapped product/customer alias resolution screen).
4. **Agreements & AI Extractions**:
   - `/agreements` and `/agreements/:id` (active immutable agreements viewer and version history).
   - `/agreements/versions/:id/diff` (version comparison and visual diff tool).
   - `/extractions`, `/extractions/:id`, and `/extractions/usage` (AI document extraction workbench, usage tracking, and cost dashboard).
5. **Calculations & Ledger Details**:
   - `/calculations/runs/:id` (calculation run breakdown and result details).
   - `/calculations/results/:id` (single line rule evaluation proof page).
   - `/calculations/rebates` (rebate volume tracker and tier progress screen).
   - `/claims/adjustments` (standalone manual adjustments ledger, beyond the reclaim action).
   - `/recovery/notifications` (in-app notifications inbox tab).
