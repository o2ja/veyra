import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, test, tokenFromMail, uniqueEmail } from "./support";

// S3 import wizard against the real stack (API, worker, ClamAV, SeaweedFS). Expected counts come from
// testdata/generated/ground_truth/expected_import_results.json and the backend's hand-derived layout-A
// totals (23 lines). SYNTHETIC data only.

const data = (rel: string) => fileURLToPath(new URL(`../../testdata/generated/${rel}`, import.meta.url));
const truth = (name: string) => JSON.parse(readFileSync(data(`ground_truth/${name}`), "utf8")) as unknown;

interface Manufacturer {
  name: string;
  code: string;
}
interface ImportResults {
  "sales_scenarios_layoutA.csv": {
    reimport_same_file: { inserted: number; changed: number; unchanged: number };
  };
  "sales_with_errors_layoutA.csv": {
    rows_total: number;
    duplicate_in_file: number;
    valid_inserted: number;
    rejected_rows: Record<string, string>;
  };
}

async function noViolations(page: Page, name: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = result.violations.map((v) => `${v.id} (${v.nodes.length})`);
  expect(summary, `${name}: ${summary.join(", ")}`).toEqual([]);
}

async function newAdmin(page: Page) {
  const admin = uniqueEmail("e2e-imports");
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Imports Admin");
  await page.getByLabel("Company name").fill("Midway Electrical Supply (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await enrolMfa(page);
}

/** The value shown next to one label in the "Row counts" list on the import page. */
const count = (page: Page, name: string) =>
  page
    .getByLabel("Row counts")
    .locator("div")
    .filter({ has: page.locator("dt", { hasText: new RegExp(`^${name}$`) }) })
    .locator("dd");

/** Wizard step 1; `file` is a testdata path to upload, or the name of an already uploaded file. */
async function startImport(page: Page, type: string, file: { upload: string } | { existing: string }) {
  await page.goto("/imports/new");
  if ("upload" in file) {
    await page.getByLabel("File to upload").setInputFiles(data(file.upload));
  } else {
    await page.getByLabel("Use a file that is already uploaded").check();
    const option = page.getByLabel("Uploaded file").locator("option", { hasText: file.existing });
    await expect(option).toHaveCount(1);
    await page.getByLabel("Uploaded file").selectOption((await option.getAttribute("value")) ?? "");
  }
  await page.getByLabel("What the file contains").selectOption({ label: type });
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page).toHaveURL(/\/imports\/[0-9a-f-]{36}$/, { timeout: 60_000 });
}

/** After step 1: accept the pre-filled mapping if one is asked for, then wait for the review step. */
async function mapIfAsked(page: Page) {
  const map = page.getByRole("heading", { name: "Map the columns" });
  const review = page.getByRole("heading", { name: "Check and confirm" });
  await expect(map.or(review)).toBeVisible({ timeout: 120_000 }); // malware scan + parse
  if (await map.isVisible()) {
    await page.getByRole("button", { name: "Apply mapping and check rows" }).click();
    await expect(review).toBeVisible({ timeout: 60_000 });
  }
}

async function confirmAndWait(page: Page) {
  await page.getByRole("button", { name: "Confirm import" }).click();
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible({ timeout: 120_000 });
}

test("import wizard: master data, layout A sales, re-import no-op, error rows, lineage, unmapped queue", async ({
  page,
}) => {
  test.setTimeout(900_000);
  await newAdmin(page);

  // master data the sales files refer to: manufacturers, branch BR01, customers, products, UoM
  await page.getByRole("link", { name: "Master data" }).click();
  for (const m of truth("master_manufacturers.json") as Manufacturer[]) {
    await page.getByLabel("Name", { exact: true }).fill(m.name);
    await page.getByLabel("Code", { exact: true }).fill(m.code);
    await page.getByRole("button", { name: "Add manufacturer" }).click();
    await expect(page.getByRole("table", { name: "Manufacturers" }).getByText(m.name)).toBeVisible();
  }
  await page.goto("/master-data/branches");
  await page.getByLabel("Name", { exact: true }).fill("Branch 01 (SYNTHETIC)");
  await page.getByLabel("Code", { exact: true }).fill("BR01");
  await page.getByRole("button", { name: "Add branch" }).click();
  await expect(page.getByRole("table", { name: "Branches" }).getByText("BR01")).toBeVisible();
  for (const [file, type] of [
    ["master/customers.csv", "Customers"],
    ["master/products.csv", "Products"],
    ["master/uom_conversions.csv", "UoM conversions"],
  ] as const) {
    await startImport(page, type, { upload: file });
    await mapIfAsked(page);
    await confirmAndWait(page);
    await expect(page.getByText("Import completed.")).toBeVisible();
    await expect(count(page, "Rejected")).toHaveText("0");
  }

  // layout A sales: 23 lines inserted
  await startImport(page, "Sales", { upload: "sales/sales_scenarios_layoutA.csv" });
  await mapIfAsked(page);
  await expect(page.getByText("All 23 rows passed the checks.")).toBeVisible();
  await noViolations(page, "import review");
  await confirmAndWait(page);
  await expect(count(page, "Inserted")).toHaveText("23");
  await expect(count(page, "Rejected")).toHaveText("0");
  await noViolations(page, "import result");
  const firstImport = page.url();

  // line browser, filtered to this import; lineage back to the file and row
  await page.getByRole("link", { name: "Browse the lines of this import" }).click();
  const lines = page.getByRole("table", { name: "Sales lines" });
  await expect(lines.getByRole("row")).toHaveCount(24); // header + 23
  await noViolations(page, "/lines/sales");
  const row = lines.getByRole("row").filter({ hasText: "INV-10001 / 1" });
  await row.getByRole("link", { name: "INV-10001 / 1" }).click();
  await expect(page.getByText("100 EA")).toBeVisible();
  await noViolations(page, "/lines/sales/:id");
  await page.getByRole("link", { name: "File, row 2" }).click();
  await expect(page.getByRole("heading", { name: "sales_scenarios_layoutA.csv" })).toBeVisible();

  // identical re-import of the same file: nothing inserted or changed
  const gt = truth("expected_import_results.json") as ImportResults;
  const again = gt["sales_scenarios_layoutA.csv"].reimport_same_file;
  await startImport(page, "Sales", { existing: "sales_scenarios_layoutA.csv" });
  await mapIfAsked(page);
  await confirmAndWait(page);
  await expect(count(page, "Inserted")).toHaveText(String(again.inserted));
  await expect(count(page, "Changed")).toHaveText(String(again.changed));
  await expect(count(page, "Unchanged")).toHaveText(String(again.unchanged));
  await expect(page.getByText("No row changed compared with earlier imports.")).toBeVisible();

  // a file with errors: rejected rows are listed and block a full import; load only the valid rows
  const bad = gt["sales_with_errors_layoutA.csv"];
  const rejected = Object.keys(bad.rejected_rows).length;
  await startImport(page, "Sales", { upload: "sales/sales_with_errors_layoutA.csv" });
  await mapIfAsked(page);
  await expect(count(page, "Rows in file")).toHaveText(String(bad.rows_total));
  await expect(count(page, "Duplicates in file")).toHaveText(String(bad.duplicate_in_file));
  await expect(count(page, "Rejected")).toHaveText(String(rejected));
  await expect(page.getByText(`${rejected} of ${bad.rows_total} rows have errors`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm import" })).toBeDisabled();
  const errors = page.getByRole("table", { name: "Row errors" });
  for (const code of new Set(Object.values(bad.rejected_rows).map((v) => v.split(" ")[0] ?? v)))
    await expect(errors.getByText(code, { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Download all row errors (CSV)" })).toBeVisible();
  await page.getByLabel(/^Load the 1 valid rows/).check();
  await confirmAndWait(page);
  await expect(page.getByText(`${rejected} rows were rejected`)).toBeVisible();
  await expect(count(page, "Inserted")).toHaveText(String(bad.valid_inserted));

  // the unknown product waits in the unmapped queue; map it to a real product
  await page.goto("/master-data/unmapped");
  await noViolations(page, "/master-data/unmapped");
  const entry = page
    .getByRole("table", { name: "Unmapped values" })
    .getByRole("row")
    .filter({ hasText: "NOPE-123" });
  await entry.getByRole("searchbox").fill("BRK-3P-100A");
  const pick = entry.getByRole("combobox");
  const product = pick.locator("option", { hasText: "BRK-3P-100A ·" });
  await expect(product).toHaveCount(1);
  await pick.selectOption((await product.getAttribute("value")) ?? "");
  await entry.getByRole("button", { name: "Map" }).click();
  await expect(page.getByText("Done. Import the file again to load the held rows.")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "NOPE-123" })).toHaveCount(0);

  // the import history lists all of it
  await page.goto("/imports");
  await expect(page.getByRole("table", { name: "Import history" }).getByRole("row")).toHaveCount(1 + 6);
  await noViolations(page, "/imports");
  await page.goto(firstImport);
  await expect(count(page, "Inserted")).toHaveText("23");
});
