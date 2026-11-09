# Synthetic Test Data Specification

> **Everything described here is SYNTHETIC (fabricated).** Companies, people, prices, agreements, and documents are invented for testing; no real customer data is used or implied. Real, anonymised documents must replace or extend these as soon as available (assumption A-12). Policy: ADR-022.
> Generator: `testdata/generate.py` (seed `20261109`, deterministic). Output: `testdata/generated/`. Ground truth: `testdata/generated/ground_truth/*.json`. Documentation self-check: `testdata/verify_business_logic_examples.py`.
> Regenerate: `python3 -I testdata/generate.py --volume 2000 [--out DIR]` (needs `openpyxl`, `reportlab`; generator-only dependencies, not part of the app).

## 1. The fictional world

| Item | Value |
|---|---|
| Distributor ("tenant") | *Midway Electrical Supply (SYNTHETIC)*, electrical vertical (ADR-019) |
| Manufacturers | `VOLT` Voltara Electric (SPA + annual purchase rebate); `BRIT` Brightline Lighting (guaranteed-margin SPA); `COPR` Coppermark Wire & Cable (flat per-foot SPA, reels, deductions); `NWND` Northwind HVAC (sales-based growth rebate; gives HVAC-style coverage) |
| Customers | ALDER, BLUFF, CEDAR, DELTA, EVERG, FOXHL, GRANI (contractors; names end with `(SYNTHETIC)`) |
| Products | 10 SKUs incl. leading-zero SKU `00418`, reel/foot wire with UoM conversion (no `CT` conversion on purpose) |
| Manufacturer settings | `master_manufacturers.json`: claim windows (60/90/60/120 days), dispute windows, tolerance 0.01, conflict policies |
| Business "run date" | 2026-07-10 (tests inject it; never the wall clock) |

All PDFs carry a red banner: *SYNTHETIC TEST DOCUMENT - FABRICATED DATA*.

## 2. Catalogue

### 2.1 Master data (`master/`)
`customers.csv`, `products.csv`, `uom_conversions.csv`.

### 2.2 Agreements (`agreements/`)

| File | Type | Purpose |
|---|---|---|
| `SYNTHETIC_spa_voltara_SPA-VOLT-2026-884.pdf` | PDF, narrative + item table | Baseline SPA: eligible customers, ship-date rule, cap, 60-day claim window, per-line rounding |
| `SYNTHETIC_rebate_voltara_REB-VOLT-2026.pdf` | PDF | Retroactive tiers (1.5/2.5/3.5%), purchases basis, returns/credits netted |
| `SYNTHETIC_rebate_northwind_REB-NWND-2026.pdf` | PDF | Sales basis; SPA lines **qualify but do not earn** (stacking in prose) |
| `SYNTHETIC_spa_missing_info_SPA-BRIT-2026-077.pdf` | PDF | **Missing information:** no end date, price, date basis, rounding. Must yield nulls/unsupported, never guesses |
| `SYNTHETIC_spa_injection_test_SPA-VOLT-2026-950.pdf` | PDF with hidden white text | **Prompt-injection test.** Hidden instruction must be ignored and flagged; deviated cost stays 50.00 |
| `SYNTHETIC_spa_coppermark_multipage_SPA-COPR-2026-220.pdf` | PDF, 48-row table over 2 pages | Table spanning pages, per-FT allowance |
| `SYNTHETIC_spa_brightline_items.xlsx` | Excel, title rows above header, different headers | Excel layout variance; header detection |
| `manual_payloads/*.json` | Agreement payloads (`BUSINESS_LOGIC.md` §3.3) | Manual-entry fixtures; also the expected result of extracting the matching documents. Includes `SPA-VOLT-2026-901` (Evergreen) used for conflict tests |

**Gaps (not yet generated):** scanned/rotated/skewed PDFs (needs image rendering; to be added in S6 or replaced by real scans), password-protected PDF, non-English document, very long (> 60 pages) document, contract with amendment pages. Track in `PROJECT_STATUS.md`.

### 2.3 Sales (`sales/`)

| File | Purpose |
|---|---|
| `sales_scenarios_layoutA.csv` | 23 hand-designed lines (`SC-xx`) with expected results; credit notes carry **positive** quantities in the file (normaliser must flip) |
| `sales_layoutB_us_formats.xlsx` | Different headers, two title rows, `MM/DD/YYYY` dates, thousands separators, values stored as text, `00418` SKU |
| `sales_with_errors_layoutA.csv` | Invalid date, missing customer, unknown SKU, negative qty on invoice, non-numeric price, zero qty, in-file duplicate |
| `sales_corrected_changed_rows.csv` | Re-export with 2 changed rows (price, quantity) for change handling; one line is in the frozen claim fixture |
| `sales_hvac_rebate_scenario.csv` | 12 lines of 100,000: 10 normal + 2 SPA (NWND sales rebate R3) |
| `sales_volume_2000.csv` | Seeded volume file (`--volume N`) with 3% price mismatches, 2% missing SPA ref, 2% ineligible customers; used for functional + performance checks |

### 2.4 Purchases (`purchases/`)
`purchases_VOLT_2026.csv`: monthly receipts totalling 990,000 by 2026-08-15, a 20,000 receipt on 2026-08-31 and a −20,000 vendor credit on 2026-09-15 (retro catch-up and claw-back).

### 2.5 Invoices (`invoices/`)
Three PDF invoices (`INV-10001`, `INV-10010`, `INV-10021`) for visual/document-viewer tests and trace drill-down. Imports use CSV/XLSX; invoice PDF *parsing* is not a Release 1 feature.

### 2.6 Remittances (`remittance/`)

| File | Purpose |
|---|---|
| `credit_memo_VOLT_CM-7781.csv` and `SYNTHETIC_credit_memo_VOLT_CM-7781.pdf` | Same content in two formats: short-pay with mapped reason, full rejection, payment net of a return adjustment (two allocations incl. a negative), lump payment (no refs), unmatched payment, one claim missing entirely. Total `7,545.00` (arithmetic check) |
| `remittance_BRIT_RA-5521.xlsx` | Exact-reference match; invoice number formatted differently (`inv-10099`) |
| `deduction_notice_COPR_DN-310.csv` | Payment **by deduction** (`channel = deduction`) |

### 2.7 Ground truth (`ground_truth/`)

| File | Used by |
|---|---|
| `expected_calculation.json` | Engine golden tests: per scenario `status`, `exception_code`, amounts, quantities, flags; plus `variants` for conflicts and amendments |
| `expected_rebates.json`, `expected_rebates_nwnd.json` | Tier tests (catch-up 10,400; claw-back −10,400; qualify-only 25,000.00); idempotency rule |
| `claim_batches_fixture.json` | Pre-built submitted batch `CB-VOLT-2026-06` (7 lines, 1 adjustment, total 10,360.00) loaded directly by reconciliation tests (claim references are assigned here so tests do not depend on numbering logic) |
| `expected_reconciliation.json` | Matching method, status, allocations, variance case type, reason code, dispute assessment, deadline (2026-09-29) |
| `expected_import_results.json`, `expected_changed_rows.json` | Import counts, row errors, duplicate/changed handling |
| `expected_extraction/*.json` | What a perfect extractor produces for each agreement document (incl. nulls for missing info, injection note) |
| `expected_volume_summary.json` | Totals/exception counts for the volume file (eligible 1,869 lines, total 972,707.50 for the default seed) |
| `master_manufacturers.json` | Manufacturer settings fixture |

## 3. Scenario table (sales scenarios and expected engine output)

Run date 2026-07-10; all agreements in `manual_payloads/` active (unless noted).

| ID | Line | Rule exercised | Expected |
|---|---|---|---|
| SC-01 | INV-10001/1 | Price difference (GL-S1) | eligible 2,200.00 |
| SC-02 | INV-10002/1 | Price mismatch (GL-S11) | exception `price_mismatch` |
| SC-03 | INV-10003/1 | Guaranteed margin 12% | eligible 448.00 (8.96/unit) |
| SC-04 | INV-10004/1 | Ship date after expiry (GL-S10) | `expired_agreement` |
| SC-05 | INV-10005/1 | Customer not eligible | `ineligible_customer` |
| SC-06 | INV-10006/1 | SPA ref required but blank | `missing_spa_reference` |
| SC-07a/b | INV-10010/1, INV-10011/1 | Cap 100 (GL-S6) | 3,120.00 and 2,080.00 (qty 40, excess 20, `partially_eligible`) |
| SC-08 / 08r | INV-10020/1, CN-5001/1 | Return before claim (GL-S9) | 312.50 (qty 25); return `netted` |
| SC-09 / 09r | INV-10021/1, CN-5002/1 | Return after claim (GL-S8) | 250.00; adjustment −50.00 (precondition: claimed) |
| SC-10 | INV-10030/1 | Customer not in list | `ineligible_customer` |
| SC-11 | INV-10031/1 | Product not an item | `ineligible_product` |
| SC-12 | INV-10032/1 | UoM conversion RL→FT (GL-S5) | 60.00 (1,500 FT) |
| SC-13 | INV-10033/1 | No CT→FT conversion | `missing_uom_conversion` |
| SC-14 (+variants b, c) | INV-10034/1 | Single agreement 200.00; engineered conflict; priority winner (GL-S12) | 200.00 / `conflicting_agreements` / 200.00 |
| SC-15 | INV-10035/1 | Claim window (GL-D1) | `outside_claim_window`, stored amount 220.00 |
| SC-16 | INV-10036/1 | Line-cost basis, blank cost | `missing_cost` |
| SC-17 | INV-10040/1 | Leading-zero SKU not an item | `ineligible_product` (SKU preserved as `00418`) |
| SC-20/21/22 | INV-10050, 10051, 10060 | Reconciliation fixtures | 1,000.00 / 1,100.00 / 660.00 |

(Golden IDs map to `BUSINESS_LOGIC.md` §14.)

## 4. Rules for using and extending synthetic data

1. Every generated file must be recognisable as synthetic (banner, `(SYNTHETIC)` names, README). Never include a real company, person, or price.
2. Expected results are derived **independently** (by hand or by a separate simple script such as `verify_business_logic_examples.py`), never by running the system under test and copying its output.
3. When a rule in `BUSINESS_LOGIC.md` changes, update the scenario, the ground truth, and the golden tests in the same change.
4. Add one scenario per bug found in the field (regression corpus) with an anonymised real-world shape, once real data exists.
5. Real customer data may be used only after anonymisation, with the customer's written consent, and is stored outside the repository.
6. Large volume files are generated, not committed, when `--volume` exceeds 10,000 rows (the default 2,000-row file may be committed).
7. Performance data: `--volume 1000000 --out <git-ignored dir>` for NFR-04 tests.
