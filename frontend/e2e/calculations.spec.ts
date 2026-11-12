import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, tokenFromMail, uniqueEmail } from "./support";

const data = (rel: string) => fileURLToPath(new URL(`../../testdata/generated/${rel}`, import.meta.url));
const truth = (name: string) => JSON.parse(readFileSync(data(`ground_truth/${name}`), "utf8")) as unknown;

interface Manufacturer {
  name: string;
  code: string;
}

async function importFile(page: Page, type: string, relPath: string) {
  await page.goto("/imports/new");
  await page.getByLabel("File to upload").setInputFiles(data(relPath));
  await page.getByLabel("What the file contains").selectOption({ label: type });
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page).toHaveURL(/\/imports\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  const map = page.getByRole("heading", { name: "Map the columns" });
  const review = page.getByRole("heading", { name: "Check and confirm" });
  await expect(map.or(review)).toBeVisible({ timeout: 120_000 });
  if (await map.isVisible()) {
    await page.getByRole("button", { name: "Apply mapping and check rows" }).click();
    await expect(review).toBeVisible({ timeout: 60_000 });
  }
  await page.getByRole("button", { name: "Confirm import" }).click();
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible({ timeout: 120_000 });
}

async function noViolations(page: Page, name: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = result.violations.map(
    (v) =>
      `${v.id} (${v.nodes.length}): ${v.nodes
        .slice(0, 3)
        .map((n) => n.target.join(" "))
        .join(" | ")}`,
  );
  expect(summary, `${name}: ${summary.join(", ")}`).toEqual([]);
}

test("S5 calculations: run, completion, exception filter, result trace, rebate tracker, axe", async ({
  page,
  browser,
}) => {
  test.setTimeout(600_000);
  const admin = uniqueEmail("e2e-calc-admin");
  const approver = uniqueEmail("e2e-calc-approver");

  // 1. Register admin + MFA
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Calc Admin");
  await page.getByLabel("Company name").fill("Voltara Calc Co (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await enrolMfa(page);

  // 2. Master data: manufacturers, branch BR01, customers, products, UoM conversions
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
  await expect(page.getByText("Branch added.")).toBeVisible();

  for (const [file, type] of [
    ["master/customers.csv", "Customers"],
    ["master/products.csv", "Products"],
    ["master/uom_conversions.csv", "UoM conversions"],
  ] as const) {
    await importFile(page, type, file);
  }

  // 3. Invite approver + MFA
  await page.getByRole("link", { name: "Invitations" }).click();
  await page.getByLabel("Email").fill(approver);
  await page.getByLabel("Role").selectOption("approver");
  await page.getByRole("button", { name: "Invite" }).click();
  await expect(page.getByText(`Invitation created for ${approver}`)).toBeVisible();

  const approverCtx = await browser.newContext();
  const approverPage = await approverCtx.newPage();
  await approverPage.goto(`/accept-invite#token=${await tokenFromMail(approver)}`);
  await approverPage.getByLabel("Your name").fill("Calc Approver");
  await approverPage.getByLabel("Password").fill(PASSWORD);
  await approverPage.getByRole("button", { name: "Accept invitation" }).click();
  await signIn(approverPage, approver);
  await enrolMfa(approverPage);

  // 4. Create & approve SPA agreement
  await page.goto("/agreement-drafts");
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const draftId = page.url().split("/").pop() ?? "";

  await page.getByLabel("Agreement type").selectOption("spa");
  await page.getByLabel("External number").fill("SPA-VOLT-2026-884");
  await page.getByLabel("Manufacturer code").fill("VOLT");
  await page.getByLabel("Name", { exact: true }).fill("Voltara contractor SPA 2026 (SYNTHETIC)");
  await page.getByLabel("Valid from").fill("2026-01-01");
  await page.getByLabel("Valid to").fill("2026-12-31");

  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "Add customer" }).click();
  }
  for (const [i, code] of ["ALDER", "BLUFF", "CEDAR"].entries()) {
    await page.getByLabel("Customer code").nth(i).fill(code);
  }
  for (const [i, it] of [
    { sku: "BRK-3P-100A", ref: "100.00", dev: "78.00", sell: "88.00" },
    { sku: "BRK-3P-60A", ref: "64.50", dev: "52.00", sell: "58.00" },
    { sku: "PNL-200A", ref: "412.00", dev: "360.00", sell: "389.00" },
  ].entries()) {
    await page.getByRole("button", { name: "Add item" }).click();
    await page.getByLabel(`Item ${i + 1} SKU`).fill(it.sku);
    await page.getByLabel(`Item ${i + 1} reference cost`).fill(it.ref);
    await page.getByLabel(`Item ${i + 1} deviated cost`).fill(it.dev);
    await page.getByLabel(`Item ${i + 1} sell price`).fill(it.sell);
  }

  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validation passed.")).toBeVisible();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

  await approverPage.goto(`/agreement-drafts/${draftId}`);
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();
  await approverCtx.close();

  // 5. Import sales CSV via import wizard
  await importFile(page, "Sales", "sales/sales_scenarios_layoutA.csv");

  // 6. Start a calculation run and wait for completion
  await page.getByRole("link", { name: "Calculations" }).click();
  await expect(page.getByRole("heading", { name: "Calculation runs" })).toBeVisible();
  await noViolations(page, "/calculations");

  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();

  const runsTable = page.getByRole("table", { name: "Calculation runs" });
  await expect(runsTable.getByText("Completed").first()).toBeVisible({ timeout: 120_000 });

  // Open run detail
  await runsTable.locator("tbody tr").first().getByRole("link").first().click();
  await expect(page).toHaveURL(/\/calculations\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await noViolations(page, "/calculations/:runId");

  // Filter exceptions
  await page.getByLabel("Status").selectOption("exception");
  const resultsTable = page.getByRole("table", { name: "Calculation results" });
  await expect(resultsTable.or(page.getByText("No matching results."))).toBeVisible();

  // Clear filter and open first result's trace
  await page.getByLabel("Status").selectOption("");
  await expect(resultsTable).toBeVisible();
  await resultsTable.locator("tbody tr").first().getByRole("link").first().click();
  await expect(page).toHaveURL(/\/calculations\/results\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: "Explain this number" })).toBeVisible();
  await noViolations(page, "/calculations/results/:resultId");

  // Rebate tracker
  await page
    .getByRole("navigation", { name: "Calculations" })
    .getByRole("link", { name: "Rebate tracker" })
    .click();
  await expect(page.getByRole("heading", { name: "Rebate tracker" })).toBeVisible();
  await noViolations(page, "/calculations/rebates");
});
