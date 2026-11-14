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

/** S10 reporting. The data is built through the real UI flow (import, SPA, calculation run, claim batch,
 * approval, submission). The dashboard figure is checked against the batch total the server shows on the batch page
 * (one submitted batch, so claimed == that batch's total_amount), never against a number typed here. The S8
 * ground-truth story (claimed 10,672.50 / collected 7,470.00 / short-paid 2,940.00) is asserted by the backend test
 * backend/tests/integration/test_reporting.py; it needs remittance reconciliation and a fixed clock, which this
 * browser flow does not reproduce. */
test("S10 reporting: dashboard figures, drill-down, trace, audit pack, analyst denied, axe", async ({
  page,
  browser,
}) => {
  test.setTimeout(480_000);
  const admin = uniqueEmail("e2e-report-admin");
  const approver = uniqueEmail("e2e-report-approver");
  const analyst = uniqueEmail("e2e-report-analyst");

  // 1. Register admin + MFA
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Reporting Admin");
  await page.getByLabel("Company name").fill("Voltara Reporting Co (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await enrolMfa(page);

  // 2. Master data
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

  // 3. Import sales CSV
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
  await approverPage.getByLabel("Your name").fill("Claims Approver");
  await approverPage.getByLabel("Password").fill(PASSWORD);
  await approverPage.getByRole("button", { name: "Accept invitation" }).click();
  await expect(approverPage).toHaveURL(/\/login\?reason=invited/);
  await signIn(approverPage, approver);
  await enrolMfa(approverPage);

  // analyst: a role without audit_pack.export
  await page.getByRole("link", { name: "Invitations" }).click();
  await page.getByLabel("Email").fill(analyst);
  await page.getByLabel("Role").selectOption("analyst");
  await page.getByRole("button", { name: "Invite" }).click();
  await expect(page.getByText(`Invitation created for ${analyst}`)).toBeVisible();
  const analystCtx = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
  const analystPage = await analystCtx.newPage();
  await analystPage.goto(`/accept-invite#token=${await tokenFromMail(analyst)}`);
  await analystPage.getByLabel("Your name").fill("Reporting Analyst");
  await analystPage.getByLabel("Password").fill(PASSWORD);
  await analystPage.getByRole("button", { name: "Accept invitation" }).click();
  await expect(analystPage).toHaveURL(/\/login\?reason=invited/);
  await signIn(analystPage, analyst);
  await enrolMfa(analystPage);

  // 5. Create and approve SPA agreement draft
  await page.goto("/agreement-drafts");
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const spaDraftId = page.url().split("/").pop() ?? "";

  await page.getByLabel("Agreement type").selectOption("spa");
  await page.getByLabel("External number").fill("SPA-VOLT-2026-884");
  await page.getByLabel("Manufacturer code").fill("VOLT");
  await page.getByLabel("Name", { exact: true }).fill("Voltara contractor SPA (SYNTHETIC)");
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
    { sku: "PNL-200A", ref: "412.00", dev: "360.00", sell: "389.00", cap: "100" },
  ].entries()) {
    await page.getByRole("button", { name: "Add item" }).click();
    await page.getByLabel(`Item ${i + 1} SKU`).fill(it.sku);
    await page.getByLabel(`Item ${i + 1} reference cost`).fill(it.ref);
    await page.getByLabel(`Item ${i + 1} deviated cost`).fill(it.dev);
    await page.getByLabel(`Item ${i + 1} sell price`).fill(it.sell);
    if (it.cap) await page.getByLabel(`Item ${i + 1} max quantity`).fill(it.cap);
  }

  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();

  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validation passed.")).toBeVisible();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

  await approverPage.goto(`/agreement-drafts/${spaDraftId}`);
  await expect(approverPage.getByRole("button", { name: "Approve draft" })).toBeVisible();
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();

  // Calculation run
  await page.getByRole("link", { name: "Calculations" }).click();
  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();
  const firstRunRow = page.getByRole("table", { name: "Calculation runs" }).locator("tbody tr").first();
  await expect(firstRunRow.locator("td").nth(1)).toHaveText("Completed", { timeout: 180_000 });

  // Claim batch: create, approve (second person), submit
  await page.goto("/claims/missed");
  await page.getByLabel("Manufacturer").selectOption({ label: "Voltara Electric (SYNTHETIC) (VOLT)" });
  await page.getByRole("button", { name: "Apply filters" }).click();
  const linesTable = page.getByRole("table", { name: "Missed claim lines" });
  await linesTable.locator("tbody tr input[type='checkbox']").first().check();
  await page.getByRole("button", { name: /Create batch from selected/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Create batch" }).click();
  await expect(page).toHaveURL(/\/claims\/batches\/[0-9a-f-]+$/, { timeout: 30_000 });
  const batchId = page.url().split("/").pop() ?? "";
  const batchTotal = (
    await page.locator("dt", { hasText: "Batch total" }).locator("xpath=following-sibling::dd").innerText()
  ).trim();
  // Ground truth SC-22 (INV-10060 line 1, expected_calculation.json): 30 x 22.00 = 660.00; the API shows 4 decimals.
  expect(batchTotal).toBe("660.0000");

  await approverPage.goto(`/claims/batches/${batchId}`);
  await approverPage.getByRole("button", { name: "Approve batch" }).click();
  await approverPage.getByRole("dialog").getByRole("button", { name: "Approve batch" }).click();
  await expect(approverPage.getByText("Approved", { exact: true })).toBeVisible({ timeout: 15_000 });

  await page.goto(`/claims/batches/${batchId}`);
  await page.getByRole("button", { name: "Submit batch" }).click();
  const submitDialog = page.getByRole("dialog");
  await submitDialog.getByLabel("External reference").fill("PORTAL-REF-REPORT");
  await submitDialog.getByRole("button", { name: "Confirm submission" }).click();
  await expect(page.getByText("Submitted", { exact: true })).toBeVisible({ timeout: 15_000 });

  // Dashboard: build the figures
  await page.goto("/dashboard");
  const build = page.getByRole("button", { name: /^(Build figures|Refresh figures)$/ });
  await expect(build).toBeVisible();
  await noViolations(page, "Dashboard before refresh");
  await build.click();
  await expect(page.getByText("Dashboard refresh queued.")).toBeVisible();
  const figures = page.getByRole("region", { name: "Key performance figures" });
  await expect(figures).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText(/^As of /)).toBeVisible();
  await noViolations(page, "Dashboard populated");

  // The claimed figure is the server's string for the single submitted batch, and links to its drill-down
  const claimedCard = figures.getByRole("link", { name: /^Claimed/ });
  await expect(claimedCard.locator("p").first()).toHaveText(batchTotal, { timeout: 120_000 });
  // every monthly figure also links to a drill-down; the month comes from the server
  const monthly = page.getByRole("table", { name: "Monthly breakdown table" });
  await expect(monthly.locator("tbody tr")).not.toHaveCount(0);
  await expect(
    monthly
      .locator("tbody tr")
      .first()
      .getByRole("link", { name: /^Claimed \d{4}-\d{2}-\d{2}: / }),
  ).toBeVisible();

  // Drill-down: figure == total == batch total, a row traces back
  await claimedCard.click();
  await expect(page).toHaveURL(/\/dashboard\/claimed/);
  await expect(page.getByRole("heading", { name: "Drill-down: Claimed" })).toBeVisible();
  await expect(page.getByText(/^As of /)).toBeVisible();
  await expect(page.getByRole("region", { name: "Metric figure" }).locator("p").first()).toHaveText(
    batchTotal,
  );
  await expect(page.getByRole("region", { name: "Drill-down total" }).locator("p").first()).toHaveText(
    batchTotal,
  );
  const items = page.getByRole("table", { name: "Claimed drill-down items" });
  await expect(items.locator("tbody tr")).not.toHaveCount(0);
  // acceptance criterion: the figure equals the sum of the rows (amounts have 4 decimals: sum in ten-thousandths)
  const amountTexts = await items.locator("tbody tr td:nth-child(5)").allInnerTexts();
  const toUnits = (t: string) => {
    const [i = "0", f = ""] = t.replaceAll(",", "").trim().split(".");
    return BigInt(i + f.padEnd(4, "0").slice(0, 4));
  };
  const sumUnits = amountTexts.reduce((acc, t) => acc + toUnits(t), 0n);
  expect(sumUnits).toBe(toUnits(batchTotal));
  await noViolations(page, "Drill-down claimed");

  // Trace chain
  await items.getByRole("link", { name: "Trace →" }).first().click();
  await expect(page).toHaveURL(/\/trace\/[a-z_]+\/[0-9a-f-]{36}$/);
  const chain = page.getByRole("region", { name: /^Trace chain/ });
  await expect(chain.locator("ol > li")).not.toHaveCount(0);
  await noViolations(page, "Trace chain");

  // A record that does not exist: the 404 is shown, not swallowed
  await page.goto("/trace/claim_line/00000000-0000-4000-8000-000000000000");
  await expect(page.getByRole("region", { name: "Record not found" })).toBeVisible();

  // Audit pack: POST, poll the job to succeeded, download offered only then
  await page.goto("/reports/audit-pack");
  await noViolations(page, "Audit pack page");
  await page.getByLabel("Manufacturer").selectOption({ label: "Voltara Electric (SYNTHETIC) (VOLT)" });
  const batchSelect = page.getByLabel("Submitted claim batch");
  await expect(batchSelect.locator("option")).toHaveCount(2);
  await batchSelect.selectOption({ index: 1 });
  const exportBtn = page.getByRole("button", { name: "Generate audit pack (XLSX)" });
  await expect(exportBtn).toBeEnabled();
  await exportBtn.click();
  const status = page.getByRole("region", { name: "Export status" });
  await expect(status.getByText("succeeded", { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(status.getByRole("button", { name: "Download audit pack (XLSX)" })).toBeVisible();
  await noViolations(page, "Audit pack succeeded");

  // Analyst: the page is denied
  await analystPage.goto("/reports/audit-pack");
  await expect(analystPage.getByRole("region", { name: "Not available" })).toBeVisible();
  await noViolations(analystPage, "Audit pack denied (analyst)");

  await analystCtx.close();
  await approverCtx.close();
});
