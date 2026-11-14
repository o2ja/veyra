import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

import {
  PASSWORD,
  batchStatus,
  clientIpHeader,
  enrolMfa,
  signIn,
  test,
  tokenFromMail,
  uniqueEmail,
} from "./support";

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

test("S7 claims: missed scan, batch creation, four-eyes approval, templates, export, submit with freeze, axe", async ({
  page,
  browser,
}) => {
  test.setTimeout(300_000);
  const admin = uniqueEmail("e2e-claims-admin");
  const approver = uniqueEmail("e2e-claims-approver");

  // 1. Register admin + MFA
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Claims Admin");
  await page.getByLabel("Company name").fill("Voltara Claims Co (SYNTHETIC)");
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

  await approverPage.goto(`/agreement-drafts/${spaDraftId}`);
  await expect(approverPage.getByRole("button", { name: "Approve draft" })).toBeVisible();
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();

  // 6. Calculation run
  await page.getByRole("link", { name: "Calculations" }).click();
  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();

  const runsTable = page.getByRole("table", { name: "Calculation runs" });
  const firstRunRow = runsTable.locator("tbody tr").first();
  await expect(firstRunRow).toBeVisible({ timeout: 30_000 });
  await expect(firstRunRow.locator("td").nth(1)).toHaveText("Completed", { timeout: 180_000 });

  // 7. Missed claims scan (/claims/missed)
  await page.getByRole("link", { name: "Claims" }).click();
  await expect(page).toHaveURL("/claims/missed");
  await expect(page.getByRole("heading", { name: "Missed claims filters" })).toBeVisible();
  await noViolations(page, "/claims/missed");

  // Filter by Voltara Ltd
  await page.getByLabel("Manufacturer").selectOption({ label: "Voltara Electric (SYNTHETIC) (VOLT)" });
  await page.getByRole("button", { name: "Apply filters" }).click();

  // Summary values shown
  await expect(page.getByText("Total claimable lines")).toBeVisible();
  const linesTable = page.getByRole("table", { name: "Missed claim lines" });
  await expect(linesTable).toBeVisible();
  const firstCheckbox = linesTable.locator("tbody tr input[type='checkbox']").first();
  await expect(firstCheckbox).toBeVisible();

  // Select first line and read its document number
  const firstDocText = (await linesTable.locator("tbody tr td").nth(1).innerText()).trim();
  expect(firstDocText).toBeTruthy();
  await firstCheckbox.check();

  const createBatchBtn = page.getByRole("button", { name: /Create batch from selected/ });
  await expect(createBatchBtn).toBeEnabled();
  await createBatchBtn.click();

  // Confirm dialog opens
  const createDialog = page.getByRole("dialog");
  await expect(createDialog).toBeVisible();
  await createDialog.getByRole("button", { name: "Create batch" }).click();

  // Navigates to /claims/batches/:batchId
  await expect(page).toHaveURL(/\/claims\/batches\/[0-9a-f-]+$/, { timeout: 30_000 });
  const batchId = page.url().split("/").pop() ?? "";
  await noViolations(page, `/claims/batches/${batchId}`);

  // Batch details: total amount and creator's four-eyes check
  await expect(batchStatus(page)).toHaveText("Draft");
  await expect(
    page.getByText("Four-eyes rule: you created this batch, so a second approver must approve it."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve batch" })).toBeDisabled();

  // Approver opens batch and approves it
  await approverPage.goto(`/claims/batches/${batchId}`);
  await expect(batchStatus(approverPage)).toHaveText("Draft");
  const approverApproveBtn = approverPage.getByRole("button", { name: "Approve batch" });
  await expect(approverApproveBtn).toBeEnabled();
  await approverApproveBtn.click();

  const approverDialog = approverPage.getByRole("dialog");
  await expect(approverDialog).toBeVisible();
  await approverDialog.getByRole("button", { name: "Approve batch" }).click();
  await expect(batchStatus(approverPage)).toHaveText("Approved", { timeout: 15_000 });

  // 8. Create export template (/claims/templates)
  await page.getByRole("link", { name: "Export templates" }).click();
  await expect(page).toHaveURL("/claims/templates");
  await noViolations(page, "/claims/templates");

  await page.getByRole("button", { name: "New template" }).click();
  await page.getByLabel("Template name").fill("VOLT Test Template");
  await page
    .getByRole("region", { name: "Create new export template" })
    .getByLabel("Manufacturer")
    .selectOption({ label: "Voltara Electric (SYNTHETIC) (VOLT)" });
  await page.getByRole("button", { name: "Save template" }).click();
  await expect(
    page.getByRole("table", { name: "Export templates" }).getByText("VOLT Test Template"),
  ).toBeVisible();

  // 9. Back to batch detail: export and submit
  await page.goto(`/claims/batches/${batchId}`);
  await expect(batchStatus(page)).toHaveText("Approved");

  // Export batch
  await page.getByRole("button", { name: "Export batch" }).click();
  const exportDialog = page.getByRole("dialog");
  await expect(exportDialog).toBeVisible();
  await exportDialog.getByLabel("Export template").selectOption({ index: 1 });
  await exportDialog.getByRole("button", { name: "Start export" }).click();
  await expect(page.getByRole("button", { name: /Download generated export/ })).toBeVisible({
    timeout: 60_000,
  });

  // Submit batch
  await page.getByRole("button", { name: "Submit batch" }).click();
  const submitDialog = page.getByRole("dialog");
  await expect(submitDialog).toBeVisible();
  await submitDialog.getByLabel("External reference").fill("PORTAL-REF-12345");
  await submitDialog.getByRole("button", { name: "Confirm submission" }).click();

  await expect(batchStatus(page)).toHaveText("Submitted", { timeout: 15_000 });
  await expect(page.getByText("Frozen")).toBeVisible();

  // 10. Check batch in batches list (/claims/batches)
  await page.getByRole("link", { name: "Claim batches" }).click();
  await expect(page).toHaveURL("/claims/batches");
  await noViolations(page, "/claims/batches");
  await expect(
    page.getByRole("table", { name: "Claim batches" }).getByText("Submitted", { exact: true }),
  ).toBeVisible();

  // 11. Verify submitted line is no longer in missed claims scan
  await page.goto("/claims/missed");
  await page.getByLabel("Manufacturer").selectOption({ label: "Voltara Electric (SYNTHETIC) (VOLT)" });
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(linesTable).toBeVisible();
  const currentRows = await linesTable.locator("tbody tr").allInnerTexts();
  expect(currentRows.some((row) => row.includes(firstDocText) && row.includes("Frozen"))).toBeFalsy();

  await approverCtx.close();
});
