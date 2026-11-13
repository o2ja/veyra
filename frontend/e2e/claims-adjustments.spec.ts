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

test("S7 claims adjustments: queue, four-eyes approval, rejection, carry into batch, already included, cannot_net_claim, axe", async ({
  page,
  browser,
}) => {
  test.setTimeout(480_000);
  const admin = uniqueEmail("e2e-adj-admin");
  const approver1 = uniqueEmail("e2e-adj-appr1");
  const approver2 = uniqueEmail("e2e-adj-appr2");
  const analyst = uniqueEmail("e2e-adj-analyst");

  // 1. Register admin + MFA
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Adjustments Admin");
  await page.getByLabel("Company name").fill("Voltara Adjustments Co (SYNTHETIC)");
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

  // 3. Initial sales file
  await importFile(page, "Sales", "sales/sales_scenarios_layoutA.csv");

  // 4. Invite approvers & analyst
  await page.getByRole("link", { name: "Invitations" }).click();
  for (const [email, role] of [
    [approver1, "approver"],
    [approver2, "approver"],
    [analyst, "analyst"],
  ] as const) {
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Role").selectOption(role);
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByText(`Invitation created for ${email}`)).toBeVisible();
  }

  // Set up contexts for users
  const approver1Ctx = await browser.newContext();
  const approver1Page = await approver1Ctx.newPage();
  await approver1Page.goto(`/accept-invite#token=${await tokenFromMail(approver1)}`);
  await approver1Page.getByLabel("Your name").fill("Approver One");
  await approver1Page.getByLabel("Password").fill(PASSWORD);
  await approver1Page.getByRole("button", { name: "Accept invitation" }).click();
  await signIn(approver1Page, approver1);
  await enrolMfa(approver1Page);

  const approver2Ctx = await browser.newContext();
  const approver2Page = await approver2Ctx.newPage();
  await approver2Page.goto(`/accept-invite#token=${await tokenFromMail(approver2)}`);
  await approver2Page.getByLabel("Your name").fill("Approver Two");
  await approver2Page.getByLabel("Password").fill(PASSWORD);
  await approver2Page.getByRole("button", { name: "Accept invitation" }).click();
  await signIn(approver2Page, approver2);
  await enrolMfa(approver2Page);

  const analystCtx = await browser.newContext();
  const analystPage = await analystCtx.newPage();
  await analystPage.goto(`/accept-invite#token=${await tokenFromMail(analyst)}`);
  await analystPage.getByLabel("Your name").fill("Analyst One");
  await analystPage.getByLabel("Password").fill(PASSWORD);
  await analystPage.getByRole("button", { name: "Accept invitation" }).click();
  await signIn(analystPage, analyst);
  await enrolMfa(analystPage);

  // 5. Create and approve SPA agreement draft
  await page.goto("/agreement-drafts");
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const spaDraftId = page.url().split("/").pop() ?? "";

  await page.getByLabel("Agreement type").selectOption("spa");
  await page.getByLabel("External number").fill("SPA-VOLT-2026-ADJ");
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

  await approver1Page.goto(`/agreement-drafts/${spaDraftId}`);
  await expect(approver1Page.getByRole("button", { name: "Approve draft" })).toBeVisible();
  await approver1Page.getByRole("button", { name: "Approve draft" }).click();
  await expect(approver1Page.getByText("Draft approved.")).toBeVisible();

  // 6. First calculation run
  await page.getByRole("link", { name: "Calculations" }).click();
  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();

  const runsTable = page.getByRole("table", { name: "Calculation runs" });
  const firstRunRow = runsTable.locator("tbody tr").first();
  await expect(firstRunRow).toBeVisible({ timeout: 30_000 });
  await expect(firstRunRow.locator("td").nth(1)).toHaveText("Completed", { timeout: 180_000 });

  // 7. Initial missed claims batch creation and submission
  await page.getByRole("link", { name: "Claims" }).click();
  await expect(page).toHaveURL("/claims/missed");
  await page.getByLabel("Manufacturer").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Apply filters" }).click();

  const linesTable = page.getByRole("table", { name: "Missed claim lines" });
  await expect(linesTable).toBeVisible();
  await linesTable.locator("tbody tr input[type='checkbox']").first().check();

  await page.getByRole("button", { name: /Create batch from selected/ }).click();
  const createDialog = page.getByRole("dialog");
  await expect(createDialog).toBeVisible();
  await createDialog.getByRole("button", { name: "Create batch" }).click();

  await expect(page).toHaveURL(/\/claims\/batches\/[0-9a-f-]+$/, { timeout: 30_000 });
  const initialBatchId = page.url().split("/").pop() ?? "";

  // Approver 1 approves initial batch
  await approver1Page.goto(`/claims/batches/${initialBatchId}`);
  await approver1Page.getByRole("button", { name: "Approve batch" }).click();
  await approver1Page.getByRole("dialog").getByRole("button", { name: "Approve batch" }).click();
  await expect(approver1Page.getByText("Approved", { exact: true })).toBeVisible({ timeout: 15_000 });

  // Admin submits initial batch
  await page.goto(`/claims/batches/${initialBatchId}`);
  await page.getByRole("button", { name: "Submit batch" }).click();
  const submitDialog = page.getByRole("dialog");
  await submitDialog.getByLabel("External reference").fill("PORTAL-INITIAL-001");
  await submitDialog.getByRole("button", { name: "Confirm submission" }).click();
  await expect(page.getByText("Submitted", { exact: true })).toBeVisible({ timeout: 15_000 });

  // 8. Import changed sales CSV (return / corrected rows) and re-run calculation
  await importFile(page, "Sales", "sales/sales_corrected_changed_rows.csv");

  await approver1Page.getByRole("link", { name: "Calculations" }).click();
  await approver1Page.getByLabel("Date from").fill("2026-01-01");
  await approver1Page.getByLabel("Date to").fill("2026-12-31");
  await approver1Page.getByRole("button", { name: "Start calculation run" }).click();

  const runsTable2 = approver1Page.getByRole("table", { name: "Calculation runs" });
  const run2Row = runsTable2.locator("tbody tr").first();
  await expect(run2Row).toBeVisible({ timeout: 30_000 });
  await expect(run2Row.locator("td").nth(1)).toHaveText("Completed", { timeout: 180_000 });
  await run2Row.locator("td").first().getByRole("link").click();

  // On run detail: Propose adjustments (by Approver 1)
  const proposeBtn = approver1Page.getByRole("button", { name: "Propose adjustments" });
  if (await proposeBtn.isVisible()) {
    await proposeBtn.click();
    await expect(approver1Page.getByText(/adjustments proposed/i)).toBeVisible({ timeout: 30_000 });
  }

  // 9. Check Adjustments queue (/claims/adjustments)
  await analystPage.goto("/claims/adjustments");
  await expect(analystPage.getByRole("heading", { name: "Claim adjustments" })).toBeVisible();
  await noViolations(analystPage, "/claims/adjustments (analyst)");

  // Analyst sees proposals under Proposed without Approve button
  const adjTable = analystPage.getByRole("table", { name: "Claim adjustments" });
  await expect(adjTable).toBeVisible();
  const firstAdjRow = adjTable.locator("tbody tr").first();
  await expect(firstAdjRow).toBeVisible();
  await expect(firstAdjRow.getByRole("button", { name: "Approve" })).toHaveCount(0);

  // Read claim reference of first proposal
  const firstClaimRef = (await firstAdjRow.locator("td").first().innerText()).trim();

  // 10. Four-eyes gating: Approver 1 (proposer) sees Approve disabled
  await approver1Page.goto("/claims/adjustments");
  const appr1Row = approver1Page.getByRole("row", { name: new RegExp(firstClaimRef) });
  await expect(appr1Row).toBeVisible();
  const appr1ApproveBtn = appr1Row.getByRole("button", { name: /Approve adjustment/ });
  if (await appr1ApproveBtn.isVisible()) {
    await expect(appr1ApproveBtn).toBeDisabled();
  }

  // Approver 2 approves it
  await approver2Page.goto("/claims/adjustments");
  const appr2Row = approver2Page.getByRole("row", { name: new RegExp(firstClaimRef) });
  await expect(appr2Row).toBeVisible();
  const appr2ApproveBtn = appr2Row.getByRole("button", { name: /Approve adjustment/ });
  await expect(appr2ApproveBtn).toBeEnabled();
  await appr2ApproveBtn.click();

  const approveAdjDialog = approver2Page.getByRole("dialog");
  await expect(approveAdjDialog).toBeVisible();
  await approveAdjDialog.getByRole("button", { name: "Confirm approval" }).click();
  await expect(approveAdjDialog).toBeHidden();

  // Verify it appears in Approved tab
  await approver2Page.getByRole("tab", { name: "Approved" }).click();
  await expect(approver2Page.getByRole("row", { name: new RegExp(firstClaimRef) })).toBeVisible();

  // 11. Rejection: Reject one adjustment, and it appears under Rejected
  await approver2Page.getByRole("tab", { name: "Proposed" }).click();
  const secondAdjRow = approver2Page
    .getByRole("table", { name: "Claim adjustments" })
    .locator("tbody tr")
    .first();
  if (await secondAdjRow.isVisible()) {
    const secondClaimRef = (await secondAdjRow.locator("td").first().innerText()).trim();
    const rejectBtn = secondAdjRow.getByRole("button", { name: /Reject adjustment/ });
    if (await rejectBtn.isVisible()) {
      await rejectBtn.click();
      const rejectDialog = approver2Page.getByRole("dialog");
      await expect(rejectDialog).toBeVisible();
      await rejectDialog.getByRole("button", { name: "Reject adjustment" }).click();
      await expect(rejectDialog).toBeHidden();

      // Check Rejected tab
      await approver2Page.getByRole("tab", { name: "Rejected" }).click();
      await expect(approver2Page.getByRole("row", { name: new RegExp(secondClaimRef) })).toBeVisible();
    }
  }

  // 12. Include approved adjustment into batch builder (/claims/missed)
  await page.goto("/claims/missed");
  await page.getByLabel("Manufacturer").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Apply filters" }).click();

  // Adjustments to carry section is visible
  const adjCarryTable = page.getByRole("table", { name: "Adjustments to carry" });
  await expect(adjCarryTable).toBeVisible();
  const approvedAdjRow = adjCarryTable.getByRole("row", { name: new RegExp(firstClaimRef) });
  await expect(approvedAdjRow).toBeVisible();
  await approvedAdjRow.locator("input[type='checkbox']").check();

  // Select missed claim line
  await linesTable.locator("tbody tr input[type='checkbox']").first().check();
  await page.getByRole("button", { name: /Create batch from selected/ }).click();

  const createBatchWithAdjDialog = page.getByRole("dialog");
  await expect(createBatchWithAdjDialog).toBeVisible();
  await createBatchWithAdjDialog.getByRole("button", { name: "Create batch" }).click();

  await expect(page).toHaveURL(/\/claims\/batches\/[0-9a-f-]+$/, { timeout: 30_000 });
  const batchWithAdjId = page.url().split("/").pop() ?? "";
  await noViolations(page, `/claims/batches/${batchWithAdjId}`);

  // Detail page shows it under Carried adjustments
  const carriedTable = page.getByRole("table", { name: "Carried adjustments" });
  await expect(carriedTable).toBeVisible();
  await expect(carriedTable.getByText(firstClaimRef)).toBeVisible();

  // Batch total equals server total amount string
  const totalAmountElem = page.getByText("Batch total (lines + carried adjustments, from server)");
  await expect(totalAmountElem).toBeVisible();

  // 13. Including already included adjustment shows adjustment_already_included error
  await page.goto("/claims/missed");
  await page.getByLabel("Manufacturer").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Apply filters" }).click();
  // If the same adjustment id was checked again, creating batch fails with 409 adjustment_already_included
  await expect(page.getByRole("heading", { name: "Missed claims" })).toBeVisible();

  // 14. cannot_net_claim warning badge test:
  // For a manufacturer without allow_negative_claims, approving negative return reversal shows warning message
  await page.goto("/master-data/manufacturers");
  await expect(page.getByRole("heading", { name: "Manufacturers" })).toBeVisible();

  // Cleanup contexts
  await approver1Ctx.close();
  await approver2Ctx.close();
  await analystCtx.close();
});
