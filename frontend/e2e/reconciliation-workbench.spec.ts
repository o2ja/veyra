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

test.describe("S8 reconciliation workbench", () => {
  test("reconciliation: ingest, staging review, workbench matching, manual match, finalise findings, reason-codes, axe", async ({
    page,
    browser,
  }) => {
    test.setTimeout(360_000);
    const admin = uniqueEmail("e2e-recon-admin");
    const approver = uniqueEmail("e2e-recon-approver");
    const viewer = uniqueEmail("e2e-recon-viewer");

    // 1. Register admin + MFA
    await page.goto("/register");
    await page.getByLabel("Your name").fill("Reconciliation Admin");
    await page.getByLabel("Company name").fill("Voltara Reconciliation Co (SYNTHETIC)");
    await page.getByLabel("Work email").fill(admin);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();
    await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();
    await signIn(page, admin);
    await enrolMfa(page);

    // 2. Setup master data (manufacturers, branch BR01, customers, products, UoM conversions)
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

    const approverCtx = await browser.newContext();
    const approverPage = await approverCtx.newPage();
    await approverPage.goto(`/accept-invite#token=${await tokenFromMail(approver)}`);
    await approverPage.getByLabel("Your name").fill("Reconciliation Approver");
    await approverPage.getByLabel("Password").fill(PASSWORD);
    await approverPage.getByRole("button", { name: "Accept invitation" }).click();
    await signIn(approverPage, approver);
    await enrolMfa(approverPage);

    // 5. Create and approve SPA agreement draft SPA-VOLT-2026-884
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

    // 7. Create and submit VOLT claim batch
    await page.getByRole("link", { name: "Claims" }).click();
    await expect(page).toHaveURL("/claims/missed");
    await page.getByLabel("Manufacturer").selectOption({ index: 1 });
    await page.getByRole("button", { name: "Apply filters" }).click();

    await expect(page.getByRole("table", { name: "Missed claim lines" })).toBeVisible();
    const createBatchBtn = page.getByRole("button", { name: "Create batch from all matching filter" });
    await expect(createBatchBtn).toBeEnabled();
    await createBatchBtn.click();

    const createDialog = page.getByRole("dialog");
    await expect(createDialog).toBeVisible();
    await createDialog.getByRole("button", { name: "Create batch" }).click();

    await expect(page).toHaveURL(/\/claims\/batches\/[0-9a-f-]+$/, { timeout: 30_000 });
    const batchId = page.url().split("/").pop() ?? "";

    // Approver approves claim batch
    await approverPage.goto(`/claims/batches/${batchId}`);
    await expect(approverPage.getByText("Draft", { exact: true })).toBeVisible();
    const approveBatchBtn = approverPage.getByRole("button", { name: "Approve batch" });
    await expect(approveBatchBtn).toBeEnabled();
    await approveBatchBtn.click();
    await approverPage.getByRole("dialog").getByRole("button", { name: "Approve batch" }).click();
    await expect(approverPage.getByText("Approved", { exact: true })).toBeVisible({ timeout: 15_000 });

    // Admin submits claim batch
    await page.goto(`/claims/batches/${batchId}`);
    await expect(page.getByText("Approved", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Submit batch" }).click();
    const submitDialog = page.getByRole("dialog");
    await expect(submitDialog).toBeVisible();
    await submitDialog.getByLabel("External reference").fill("VOLT-BATCH-01");
    await submitDialog.getByRole("button", { name: "Confirm submission" }).click();
    await expect(page.getByText("Submitted", { exact: true })).toBeVisible({ timeout: 15_000 });

    // 8. Reason codes configuration (/reconciliation/reason-codes)
    await page.goto("/reconciliation/reason-codes");
    await noViolations(page, "Reconciliation reason-codes");
    await page.getByRole("combobox", { name: "Manufacturer" }).selectOption({ index: 1 });

    // Add E12 -> rejected_price_mismatch
    await page.getByRole("button", { name: "Add mapping" }).click();
    await page.getByLabel("External code row 1").fill("E12");
    await page.getByLabel("External description row 1").fill("Price discrepancy");
    await page.getByLabel("Controlled reason code row 1").selectOption("rejected_price_mismatch");

    // Add E07 -> rejected_quantity_exceeds_authorization
    await page.getByRole("button", { name: "Add mapping" }).click();
    await page.getByLabel("External code row 2").fill("E07");
    await page.getByLabel("External description row 2").fill("Quantity exceeds cap");
    await page
      .getByLabel("Controlled reason code row 2")
      .selectOption("rejected_quantity_exceeds_authorization");

    await page.getByRole("button", { name: "Save mappings" }).click();
    await expect(page.getByText("Reason code mappings saved successfully.")).toBeVisible();

    // 9. Invite viewer user & verify 403 / permission restrictions
    await page.getByRole("link", { name: "Invitations" }).click();
    await page.getByLabel("Email").fill(viewer);
    await page.getByLabel("Role").selectOption("viewer");
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByText(`Invitation created for ${viewer}`)).toBeVisible();

    const viewerCtx = await browser.newContext();
    const viewerPage = await viewerCtx.newPage();
    await viewerPage.goto(`/accept-invite#token=${await tokenFromMail(viewer)}`);
    await viewerPage.getByLabel("Your name").fill("Reconciliation Viewer");
    await viewerPage.getByLabel("Password").fill(PASSWORD);
    await viewerPage.getByRole("button", { name: "Accept invitation" }).click();
    await signIn(viewerPage, viewer);
    await enrolMfa(viewerPage);

    // Viewer check on list page: no "New remittance" button
    await viewerPage.goto("/reconciliation");
    await expect(viewerPage.getByRole("link", { name: "New remittance" })).toHaveCount(0);
    await noViolations(viewerPage, "Reconciliation list (viewer)");

    // Viewer check on new remittance page: permission denied
    await viewerPage.goto("/reconciliation/new");
    await expect(viewerPage.getByText(/permission denied|don't have permission/i)).toBeVisible();

    // 10. Ingest: Admin uploads remittance file
    await page.goto("/reconciliation");
    await noViolations(page, "Reconciliation list (admin)");
    await page.getByRole("link", { name: "New remittance" }).click();
    await expect(page).toHaveURL("/reconciliation/new");
    await noViolations(page, "Reconciliation new upload");

    const remittanceFile = data("remittance/credit_memo_VOLT_CM-7781.csv");
    await page.getByLabel("Remittance file").setInputFiles(remittanceFile);
    await page.getByRole("combobox", { name: "Manufacturer" }).selectOption({ index: 1 });
    await page.getByRole("combobox", { name: "Document type" }).selectOption("credit_memo");
    await page.getByRole("combobox", { name: "Channel" }).selectOption("credit_memo");
    await page.getByLabel("Document number").fill("CM-7781");

    await page.getByRole("button", { name: "Create and ingest remittance" }).click();

    // Ingest job completes and navigates to remittance detail
    await expect(page).toHaveURL(/\/reconciliation\/[0-9a-f-]+$/, { timeout: 60_000 });
    const remittanceUrl = page.url();

    // 11. Test duplicate remittance scenario
    await page.goto("/reconciliation/new");
    await page.getByLabel("Remittance file").setInputFiles(remittanceFile);
    await page.getByRole("combobox", { name: "Manufacturer" }).selectOption({ index: 1 });
    await page.getByRole("combobox", { name: "Document type" }).selectOption("credit_memo");
    await page.getByRole("combobox", { name: "Channel" }).selectOption("credit_memo");
    await page.getByLabel("Document number").fill("CM-7781");
    await page.getByRole("button", { name: "Create and ingest remittance" }).click();
    await expect(page.getByRole("alert")).toBeVisible();

    // 12. Viewer on detail page: review is limited to reconcilers
    await viewerPage.goto(remittanceUrl);
    await expect(viewerPage.getByText("Review is limited to reconcilers")).toBeVisible();
    await expect(viewerPage.getByRole("button", { name: "Accept remittance" })).toHaveCount(0);
    await expect(viewerPage.getByRole("button", { name: "Run matching" })).toHaveCount(0);

    // 13. Admin staging review and accept
    await page.goto(remittanceUrl);
    await noViolations(page, "Remittance staging review");
    await page.getByLabel("Document total").fill("7545.00");
    await page.getByRole("button", { name: "Accept remittance" }).click();

    const acceptDialog = page.getByRole("dialog", { name: "Accept remittance" });
    await expect(acceptDialog).toBeVisible();
    await acceptDialog.getByRole("button", { name: "Accept" }).click();

    // 14. Workbench matching
    await expect(page.getByText("Reconciliation workbench")).toBeVisible({ timeout: 60_000 });
    await noViolations(page, "Remittance workbench");

    await page.getByRole("button", { name: "Run matching" }).click();
    await expect(page.getByRole("region", { name: "Remittance line 1" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("region", { name: "Remittance line 5" })).toBeVisible();

    // 15. Stale tab 409 invalid_state scenario & confirm Line 5
    const tab2 = await page.context().newPage();
    await tab2.goto(remittanceUrl);
    await expect(tab2.getByRole("region", { name: "Remittance line 5" })).toBeVisible();

    // Page 1 confirms Line 5
    const line5InPage = page.getByRole("region", { name: "Remittance line 5" });
    await line5InPage.getByRole("button", { name: "Confirm" }).click();
    await expect(line5InPage.getByRole("button", { name: "Confirm" })).toHaveCount(0);

    // Tab 2 attempts to reject the already-confirmed Line 5 match -> 409 invalid_state
    const line5InTab2 = tab2.getByRole("region", { name: "Remittance line 5" });
    await line5InTab2.getByRole("button", { name: "Reject" }).click();
    await expect(
      tab2.getByText(
        "Stale revision detected: this match was updated in another session. The view has been refreshed.",
      ),
    ).toBeVisible();
    await tab2.close();

    // 16. Manual match Line 4 -> CL-4 200.00 -> short_paid 50.00
    const line4Region = page.getByRole("region", { name: "Remittance line 4" });
    await line4Region.getByRole("button", { name: "Manual match" }).click();

    const manualMatchDialog = page.getByRole("dialog", { name: "Manual match" });
    await expect(manualMatchDialog).toBeVisible();
    await manualMatchDialog.getByLabel("Submitted claim batch:").selectOption({ index: 1 });
    await manualMatchDialog.getByLabel("Claim line:").selectOption({ index: 4 });
    await manualMatchDialog.getByLabel("Allocated amount").fill("200.00");
    await manualMatchDialog.getByRole("button", { name: "Apply match" }).click();
    await expect(manualMatchDialog).not.toBeVisible();

    // 17. Early finalisation check: server returns 409 uncategorised_lines
    await page.getByRole("button", { name: "Finalise" }).click();
    const finaliseDialog = page.getByRole("dialog", { name: "Finalise reconciliation" });
    await expect(finaliseDialog).toBeVisible();
    await finaliseDialog.getByRole("button", { name: "Confirm finalisation" }).click();

    await expect(finaliseDialog.getByRole("alert")).toBeVisible();
    await expect(
      finaliseDialog.getByText("Match or categorise every remittance line before finalising."),
    ).toBeVisible();
    await expect(finaliseDialog.getByText(/lines: 1 lines/i)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(finaliseDialog).not.toBeVisible();

    // 18. Categorise ONLY Line 6
    const line6Region = page.getByRole("region", { name: "Remittance line 6" });
    await line6Region.getByRole("button", { name: "Mark as unmatched payment" }).click();
    await expect(line6Region.getByText("Categorised: unmatched_payment")).toBeVisible();

    // 19. Finalise reconciliation
    await page.getByRole("button", { name: "Finalise" }).click();
    await expect(finaliseDialog).toBeVisible();
    await finaliseDialog.getByRole("button", { name: "Confirm finalisation" }).click();

    await expect(
      page.getByText(
        "This remittance has been finalised and reconciled. Matches and allocations are immutable.",
      ),
    ).toBeVisible({ timeout: 60_000 });
    await noViolations(page, "Remittance reconciled findings");

    // 20. Reconciled findings validation
    const findingsTable = page.getByRole("table", { name: "Reconciliation findings" });
    await expect(findingsTable).toBeVisible();

    // Line 1: short_paid 150.00
    await expect(findingsTable.getByRole("row", { name: /Short-paid.*150\.00/i })).toBeVisible();

    // Line 3: rejected 2080.00
    await expect(findingsTable.getByRole("row", { name: /Rejected.*2080\.00/i })).toBeVisible();

    // Line 4: short_paid 50.00
    await expect(findingsTable.getByRole("row", { name: /Short-paid.*50\.00/i })).toBeVisible();

    // Line 6: unmatched_payment 75.00
    await expect(findingsTable.getByRole("row", { name: /Unmatched payment.*75\.00/i })).toBeVisible();

    // Line 7: unpaid 660.00
    await expect(findingsTable.getByRole("row", { name: /Unpaid.*660\.00/i })).toBeVisible();

    await viewerCtx.close();
    await approverCtx.close();
  });
});
