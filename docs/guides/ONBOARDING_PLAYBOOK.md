# Onboarding Playbook

How to bring a new distributor onto Veyra Recover, and what data to ask for. Status on `main` (2026-11-13): steps 1 to 6 work today. Claims, remittance and recovery (S7 to S10) are **not in Release 1 yet**, so a pilot can currently reach "calculated, reviewed exceptions", not "claim submitted and recovered".

Sources: `docs/PRODUCT_REQUIREMENTS.md` section 1.4, `backend/src/rebate/modules/ingestion/validators.py` (the field list below is taken from it), `docs/TEST_DATA_SPEC.md` (example shapes), `docs/SECURITY.md` section 4.

## Steps

1. **Agree scope.** Which manufacturers, which agreement types (SPA or rebate), which period. Confirm there is no card or bank account data in the files; do not send any.
2. **Create the workspace and users.** The first person registers (becomes admin), sets business time zone, MFA, four-eyes and retention in Settings (retention is stored; automatic deletion is not in Release 1 yet), then invites the others with the right roles (`USER_GUIDE.md`).
3. **Master data.** Import customers, products, unit conversions; create manufacturers and set each one's claim window (days and basis) and tolerance. Resolve the Unmapped queue.
4. **History.** Import sales lines (invoices and credit notes) and purchase lines for the rebate period. Start with a one-month sample, fix mapping issues, then load the rest.
5. **Agreements.** Enter each agreement as a draft (manual, SPA items spreadsheet, or AI extraction only if enabled), validate, have an approver approve it.
6. **Calculate and review.** Run a calculation; walk through the exceptions with the customer's finance contact and compare a few lines by hand against their own numbers before trusting the totals.

## Data requirements checklist

General, for every file: CSV (UTF-8, UTF-8 BOM, Windows-1252 or UTF-16 with BOM) or XLSX, one header row (found within the first 30 rows; title rows above are tolerated), at most 200 columns, no macros (`.xlsm`/`.xls` are refused), spreadsheets up to 50 MB. Dates can be `MM/DD/YYYY`-style or ISO; tell us the day/month order and decimal separator if not US style. Keep SKU and code columns as text. Same file twice is harmless.

"Required" means the import will not accept a row without it. Column names are examples; your own headers can be mapped.

| File | Required | Useful, optional |
|---|---|---|
| **Customers** | customer code, customer name | type (contractor, end_user, branch, other), parent customer code, external id, active |
| **Products** | SKU, manufacturer code, base unit of measure | description, category, manufacturer part number, list cost, active |
| **Unit conversions** | from unit, to unit, factor | SKU (blank = applies to all products) |
| **Sales lines** | document number, line number, invoice date, customer code, SKU, quantity, unit of measure, unit price, extended amount | document type (invoice or credit note), ship date, customer name, branch code, description, unit cost, replacement cost, SPA reference, project reference, currency, original document and line number (credit notes) |
| **Purchase lines** | document number, line number, document date, manufacturer code, SKU, quantity, unit of measure, unit cost, extended amount | document type (receipt or vendor credit), PO number, currency |
| **SPA items** (spreadsheet template) | SKU (required); optional as the agreement type needs: deviated cost, reference cost, sell price, rate, flat amount, max quantity, claim unit. Customer scope is set on the draft, not in the file | see `docs/TEST_DATA_SPEC.md` for the sample layout |
| **Agreement documents** | the signed agreement or amendment as PDF or spreadsheet, all pages | the manufacturer's terms letter, tier tables |

Points to confirm with the customer before the first import:

- **Cost basis.** Which cost is the manufacturer's deviated cost, and does the sales file carry line cost? If cost is blank where an agreement needs it, the line becomes the exception `missing_cost`.
- **Credit notes and returns.** Quantities may be positive in the source file; Veyra stores them negative. Each credit note line should reference the original invoice line.
- **Units.** Every unit used in sales or purchase lines needs a conversion to the product's base unit where they differ (otherwise `missing_uom_conversion`).
- **Ship date vs invoice date** (which one the agreement uses) and the manufacturer's claim window.
- **Fiscal period and time zone** of the business dates.
- **SPA numbers** that appear on invoices, if the agreement requires one.
- **Amended or retroactive terms**, so versions get the right dates.
- **History depth** needed for rebate tiers (a full agreement year of sales or purchases).

Known limits: invoice PDFs are not parsed (only CSV/XLSX are imported). Scanned, rotated, password-protected or very long (over 60 pages) agreement PDFs have not been tested (K-1 in `docs/PROJECT_STATUS.md`). No real customer files have been seen yet (K-2); expect to adjust this list after the first pilot.

## Data handling

Send data only through the app's upload (never by email). Pilot uploads are real customer data: the production environment, backups and the restore procedure must be in place first (`OPERATOR_GUIDE.md`, `docs/DEPLOYMENT.md` section 9).
