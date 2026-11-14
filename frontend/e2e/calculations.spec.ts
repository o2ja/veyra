import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

import { PASSWORD, clientIpHeader, enrolMfa, signIn, test, tokenFromMail, uniqueEmail } from "./support";

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

test("S5 calculations: run, completion, exception filter, result trace, propose adjustments, draft preview, rebate tracker, axe", async ({
  page,
  browser,
}) => {
  test.setTimeout(300_000);
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

  // 3. Import sales CSV before agreement preview so preview has sample lines
  await importFile(page, "Sales", "sales/sales_scenarios_layoutA.csv");

  // 4. Invite approver + MFA
  await page.getByRole("link", { name: "Invitations" }).click();
  await page.getByLabel("Email").fill(approver);
  await page.getByLabel("Role").selectOption("approver");
  await page.getByRole("button", { name: "Invite" }).click();
  await expect(page.getByText(`Invitation created for ${approver}`)).toBeVisible();

  const approverCtx = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
  const approverPage = await approverCtx.newPage();
  await approverPage.goto(`/accept-invite#token=${await tokenFromMail(approver)}`);
  await approverPage.getByLabel("Your name").fill("Calc Approver");
  await approverPage.getByLabel("Password").fill(PASSWORD);
  await approverPage.getByRole("button", { name: "Accept invitation" }).click();
  await expect(approverPage).toHaveURL(/\/login\?reason=invited/);
  await signIn(approverPage, approver);
  await enrolMfa(approverPage);

  // 5. Create SPA draft, run DraftPreviewPanel, then submit & approve
  await page.goto("/agreement-drafts");
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const spaDraftId = page.url().split("/").pop() ?? "";

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

  // Item 13: Draft preview panel
  const previewRegion = page.getByRole("region", { name: "Calculation preview" });
  await previewRegion.getByRole("button", { name: "Run preview" }).click();
  await expect(previewRegion.getByText("Preview — not saved", { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  const previewTable = previewRegion.getByRole("table", { name: "Preview lines" });
  await expect(previewTable).toBeVisible();
  expect(await previewTable.locator("tbody tr").count()).toBeGreaterThanOrEqual(1);

  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validation passed.")).toBeVisible();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

  await approverPage.goto(`/agreement-drafts/${spaDraftId}`);
  await expect(approverPage.getByRole("button", { name: "Approve draft" })).toBeVisible();
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();

  // 6. Create & approve Rebate draft REB-VOLT-2026 (Item 14: populated rebate tracker row)
  await page.goto("/agreement-drafts");
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const rebDraftId = page.url().split("/").pop() ?? "";

  await page.getByLabel("Agreement type").selectOption("rebate");
  await page.getByLabel("External number").fill("REB-VOLT-2026");
  await page.getByLabel("Manufacturer code").fill("VOLT");
  await page.getByLabel("Name", { exact: true }).fill("Voltara annual volume rebate 2026 (SYNTHETIC)");
  await page.getByLabel("Valid from").fill("2026-01-01");
  await page.getByLabel("Valid to").fill("2026-12-31");
  await page.getByLabel("Volume basis").selectOption("sales");
  await page.getByLabel("Tier metric").selectOption("amount");
  await page.getByLabel("Tier mode").selectOption("retroactive");
  await page.getByLabel("SPA lines").selectOption("include");
  await page.getByLabel("Product scope").selectOption("all");
  await page.getByLabel("Measurement period kind").fill("calendar_year");
  await page.getByLabel("Measurement period start").fill("2026-01-01");
  await page.getByLabel("Measurement period end").fill("2026-12-31");

  for (const [i, tier] of [
    { bound: "0", rate: "0.015" },
    { bound: "1000000", rate: "0.025" },
    { bound: "2000000", rate: "0.035" },
  ].entries()) {
    await page.getByRole("button", { name: "Add tier" }).click();
    await page.getByLabel("Lower bound").nth(i).fill(tier.bound);
    await page.getByLabel("Rate").nth(i).fill(tier.rate);
  }
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validation passed.")).toBeVisible();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

  await approverPage.goto(`/agreement-drafts/${rebDraftId}`);
  await expect(approverPage.getByRole("button", { name: "Approve draft" })).toBeVisible();
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();
  await approverCtx.close();

  // 7. Start a calculation run and wait up to 3 min for Completed on the status cell (Item 17)
  await page.getByRole("link", { name: "Calculations" }).click();
  await expect(page.getByRole("heading", { name: "Calculation runs" })).toBeVisible();
  await noViolations(page, "/calculations");

  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();

  const runsTable = page.getByRole("table", { name: "Calculation runs" });
  const firstRunRow = runsTable.locator("tbody tr").first();
  await expect(firstRunRow).toBeVisible({ timeout: 30_000 });
  await expect(firstRunRow.locator("td").nth(1)).toHaveText("Completed", { timeout: 180_000 });

  // Open run detail
  const runLink = firstRunRow.getByRole("link").first();
  await expect(runLink).toBeVisible();
  await runLink.click();
  await expect(page).toHaveURL(/\/calculations\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
  await noViolations(page, "/calculations/:runId");

  // Item 12: Propose adjustments
  const proposeBtn = page.getByRole("button", { name: "Propose adjustments" });
  await expect(proposeBtn).toBeVisible();
  await proposeBtn.click();
  const proposalsRegion = page.getByRole("region", { name: "Proposed adjustments" });
  await expect(proposalsRegion.getByText(/Compared \d+ item\(s\)/)).toBeVisible({ timeout: 30_000 });
  await expect(
    proposalsRegion
      .getByRole("table", { name: "Proposed adjustments" })
      .or(proposalsRegion.getByText("No adjustments proposed.")),
  ).toBeVisible();

  // Item 3: Filter exceptions, assert every row's Status & Exception code, open trace & expand
  await page.getByLabel("Status").selectOption("exception");
  const resultsTable = page.getByRole("table", { name: "Calculation results" });
  await expect(resultsTable).toBeVisible();
  const exceptionRows = resultsTable.locator("tbody tr");
  const exCount = await exceptionRows.count();
  expect(exCount).toBeGreaterThanOrEqual(1);
  for (let i = 0; i < exCount; i++) {
    const row = exceptionRows.nth(i);
    await expect(row.locator("td").nth(3)).toHaveText("Exception");
    const exCode = (await row.locator("td").nth(4).innerText()).trim();
    expect(exCode).not.toBe("");
    expect(exCode).not.toBe("—");
  }

  const firstExLink = exceptionRows.first().getByRole("link").first();
  await expect(firstExLink).toBeVisible();
  await firstExLink.click();
  await expect(page).toHaveURL(/\/calculations\/results\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: "Explain this number" })).toBeVisible();
  await noViolations(page, "/calculations/results/:resultId");

  const traceRegion = page.getByRole("region", { name: "Calculation trace" });
  const collapseBtn = traceRegion.getByRole("button", { name: "Collapse all" });
  if (await collapseBtn.isVisible()) {
    await collapseBtn.click();
  }
  const firstExpander = traceRegion.locator("ul button[aria-expanded]").first();
  await expect(firstExpander).toBeVisible();
  await expect(firstExpander).toHaveAttribute("aria-expanded", "false");
  await firstExpander.click();
  await expect(firstExpander).toHaveAttribute("aria-expanded", "true");
  await expect(firstExpander.locator("..").locator("ul li").first()).toBeVisible();

  // Item 14: Rebate tracker with populated tracker row and ledger row
  await page
    .getByRole("navigation", { name: "Calculations" })
    .getByRole("link", { name: "Rebate tracker" })
    .click();
  await expect(page.getByRole("heading", { name: "Rebate tracker" })).toBeVisible();
  await expect(page.getByRole("link", { name: "REB-VOLT-2026" }).first()).toBeVisible();
  const accrualsTable = page.getByRole("table", { name: "Rebate accruals" });
  await expect(accrualsTable).toBeVisible();
  expect(await accrualsTable.locator("tbody tr").count()).toBeGreaterThanOrEqual(1);
  await noViolations(page, "/calculations/rebates");
});
