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

const escapeRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Digit grouping only, the same display rule as the UI (no arithmetic). */
function grouping(value: string): string {
  const [int = "", frac] = value.split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return frac === undefined ? grouped : `${grouped}.${frac}`;
}

async function setAllowNegativeClaims(page: Page, manufacturerName: string, on: boolean) {
  await page.goto("/master-data/manufacturers");
  await page
    .getByRole("table", { name: "Manufacturers" })
    .getByRole("link", { name: manufacturerName })
    .click();
  await page.getByLabel("Allow negative claims").setChecked(on);
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
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
  const approver1Ctx = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
  const approver1Page = await approver1Ctx.newPage();
  await approver1Page.goto(`/accept-invite#token=${await tokenFromMail(approver1)}`);
  await approver1Page.getByLabel("Your name").fill("Approver One");
  await approver1Page.getByLabel("Password").fill(PASSWORD);
  await approver1Page.getByRole("button", { name: "Accept invitation" }).click();
  await expect(approver1Page).toHaveURL(/\/login\?reason=invited/);
  await signIn(approver1Page, approver1);
  await enrolMfa(approver1Page);

  const approver2Ctx = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
  const approver2Page = await approver2Ctx.newPage();
  await approver2Page.goto(`/accept-invite#token=${await tokenFromMail(approver2)}`);
  await approver2Page.getByLabel("Your name").fill("Approver Two");
  await approver2Page.getByLabel("Password").fill(PASSWORD);
  await approver2Page.getByRole("button", { name: "Accept invitation" }).click();
  await expect(approver2Page).toHaveURL(/\/login\?reason=invited/);
  await signIn(approver2Page, approver2);
  await enrolMfa(approver2Page);

  const analystCtx = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
  const analystPage = await analystCtx.newPage();
  await analystPage.goto(`/accept-invite#token=${await tokenFromMail(analyst)}`);
  await analystPage.getByLabel("Your name").fill("Analyst One");
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
  const volt = (truth("master_manufacturers.json") as Manufacturer[]).find((m) => m.code === "VOLT");
  if (!volt) throw new Error("VOLT manufacturer missing from ground truth");
  const voltLabel = `${volt.name} (${volt.code})`;

  await page.getByRole("link", { name: "Calculations" }).click();
  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();

  const runsTable = page.getByRole("table", { name: "Calculation runs" });
  const firstRunRow = runsTable.getByRole("row").nth(1);
  await expect(firstRunRow).toBeVisible({ timeout: 30_000 });
  await expect(firstRunRow).toContainText("Completed", { timeout: 180_000 });
  // Compare by href: UUIDv7 ids created within a minute share their 8-character prefix.
  const firstRunHref = await firstRunRow.getByRole("link").first().getAttribute("href");

  // 7. Initial missed claims batch creation and submission
  await page.getByRole("link", { name: "Claims" }).click();
  await expect(page).toHaveURL("/claims/missed");
  await page.getByLabel("Manufacturer").selectOption({ label: voltLabel });
  await page.getByRole("button", { name: "Apply filters" }).click();

  const linesTable = page.getByRole("table", { name: "Missed claim lines" });
  await expect(linesTable).toBeVisible();
  await linesTable
    .getByRole("checkbox", { name: /^Select / })
    .first()
    .check();

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
  await expect(batchStatus(approver1Page)).toHaveText("Approved", { timeout: 15_000 });

  // Admin submits initial batch
  await page.goto(`/claims/batches/${initialBatchId}`);
  await page.getByRole("button", { name: "Submit batch" }).click();
  const submitDialog = page.getByRole("dialog");
  await submitDialog.getByLabel("External reference").fill("PORTAL-INITIAL-001");
  await submitDialog.getByRole("button", { name: "Confirm submission" }).click();
  await expect(batchStatus(page)).toHaveText("Submitted", { timeout: 15_000 });

  // 8. Import changed sales CSV (return / corrected rows) and re-run calculation
  await importFile(page, "Sales", "sales/sales_corrected_changed_rows.csv");

  await approver1Page.getByRole("link", { name: "Calculations" }).click();
  await approver1Page.getByLabel("Date from").fill("2026-01-01");
  await approver1Page.getByLabel("Date to").fill("2026-12-31");
  await approver1Page.getByRole("button", { name: "Start calculation run" }).click();

  const runsTable2 = approver1Page.getByRole("table", { name: "Calculation runs" });
  // The list still shows run 1 until the new run appears, so wait for a row that is not run 1.
  const run2Row = runsTable2
    .locator("tbody tr")
    .filter({ hasNot: approver1Page.locator(`a[href="${firstRunHref}"]`) })
    .first();
  await expect(run2Row).toBeVisible({ timeout: 30_000 });
  await expect(run2Row).toContainText("Completed", { timeout: 180_000 });
  await run2Row.getByRole("link").first().click();

  // On run detail: Propose adjustments (by Approver 1)
  const proposeBtn = approver1Page.getByRole("button", { name: "Propose adjustments" });
  await expect(proposeBtn).toBeVisible();
  await proposeBtn.click();
  const proposedSummary = approver1Page.getByText(/adjustment\(s\)\s+proposed/);
  await expect(proposedSummary).toBeVisible({ timeout: 30_000 });
  const proposedCount = /(\d+)\s+adjustment\(s\)\s+proposed/.exec(await proposedSummary.innerText());
  expect(proposedCount, "proposal count text").not.toBeNull();
  expect(Number(proposedCount?.[1])).toBeGreaterThan(0);

  // 9. Adjustments queue (/claims/adjustments)
  await analystPage.goto("/claims/adjustments");
  await expect(analystPage.getByRole("heading", { name: "Claim adjustments" })).toBeVisible();
  await noViolations(analystPage, "/claims/adjustments (analyst)");

  // Analyst sees proposals under Proposed without Approve or Reject buttons
  const adjTable = analystPage.getByRole("table", { name: "Claim adjustments" });
  await expect(adjTable).toBeVisible();
  const firstAdjRow = adjTable.getByRole("row").nth(1);
  await expect(firstAdjRow).toBeVisible();
  await expect(firstAdjRow.getByRole("button", { name: /Approve/ })).toHaveCount(0);
  await expect(firstAdjRow.getByRole("button", { name: /Reject/ })).toHaveCount(0);

  // Claim reference of the first proposal (first cell of its row)
  const firstClaimRef = (await firstAdjRow.getByRole("cell").first().innerText()).trim();
  expect(firstClaimRef).not.toBe("");
  const firstRefRe = new RegExp(escapeRe(firstClaimRef));

  // 10. Four-eyes gating: Approver 1 (proposer) sees Approve disabled
  await approver1Page.goto("/claims/adjustments");
  const appr1Row = approver1Page.getByRole("row", { name: firstRefRe });
  await expect(appr1Row).toBeVisible();
  await expect(appr1Row.getByRole("button", { name: /Approve adjustment/ })).toBeDisabled();

  // 11. cannot_net_claim: with negative claims off, approving a negative return reversal is refused by the server
  await setAllowNegativeClaims(page, volt.name, false);
  await approver2Page.goto("/claims/adjustments");
  await approver2Page.getByLabel("Manufacturer").selectOption({ label: voltLabel });
  await approver2Page.getByLabel("Adjustment type").selectOption({ label: "Return reversal" });
  const negativeRow = approver2Page
    .getByRole("table", { name: "Claim adjustments" })
    .getByRole("row", { name: /\s-\d/ })
    .first();
  await expect(negativeRow).toBeVisible();
  await negativeRow.getByRole("button", { name: /Approve adjustment/ }).click();
  const netDialog = approver2Page.getByRole("dialog");
  await netDialog.getByRole("button", { name: "Confirm approval" }).click();
  await expect(netDialog).toBeHidden();
  // The server's own message (backend/src/rebate/modules/claims/adjustments.py, require_net_claimable)
  await expect(
    negativeRow.getByText("Discuss with the manufacturer; it does not accept negative claims."),
  ).toBeVisible();
  await setAllowNegativeClaims(page, volt.name, true);

  // Approver 2 approves the first proposal
  await approver2Page.goto("/claims/adjustments");
  const appr2Row = approver2Page.getByRole("row", { name: firstRefRe });
  await expect(appr2Row).toBeVisible();
  const appr2ApproveBtn = appr2Row.getByRole("button", { name: /Approve adjustment/ });
  await expect(appr2ApproveBtn).toBeEnabled();
  await appr2ApproveBtn.click();

  const approveAdjDialog = approver2Page.getByRole("dialog");
  await expect(approveAdjDialog).toBeVisible();
  await approveAdjDialog.getByRole("button", { name: "Confirm approval" }).click();
  await expect(approveAdjDialog).toBeHidden();

  // It appears in the Approved tab, reached with the keyboard (WAI-ARIA tabs)
  await approver2Page.getByRole("tab", { name: "Proposed" }).focus();
  await approver2Page.keyboard.press("ArrowRight");
  await expect(approver2Page.getByRole("tab", { name: "Approved" })).toHaveAttribute("aria-selected", "true");
  await expect(approver2Page.getByRole("row", { name: firstRefRe })).toBeVisible();

  // 12. Rejection: reject another proposal, and it appears under Rejected
  await approver2Page.getByRole("tab", { name: "Proposed" }).click();
  const secondAdjRow = approver2Page
    .getByRole("table", { name: "Claim adjustments" })
    .getByRole("row")
    .nth(1);
  await expect(secondAdjRow).toBeVisible();
  const secondClaimRef = (await secondAdjRow.getByRole("cell").first().innerText()).trim();
  expect(secondClaimRef).not.toBe("");
  await secondAdjRow.getByRole("button", { name: /Reject adjustment/ }).click();
  const rejectDialog = approver2Page.getByRole("dialog");
  await expect(rejectDialog).toBeVisible();
  await rejectDialog.getByRole("button", { name: "Reject adjustment" }).click();
  await expect(rejectDialog).toBeHidden();
  await approver2Page.getByRole("tab", { name: "Rejected" }).click();
  await expect(approver2Page.getByRole("row", { name: new RegExp(escapeRe(secondClaimRef)) })).toBeVisible();

  // 13. Carry the approved adjustment into a batch. A second, stale page opened first keeps it selected.
  const stalePage = await page.context().newPage();
  await stalePage.goto("/claims/missed");
  await stalePage.getByLabel("Manufacturer").selectOption({ label: voltLabel });
  await stalePage.getByRole("button", { name: "Apply filters" }).click();
  await stalePage
    .getByRole("table", { name: "Adjustments to carry" })
    .getByRole("row", { name: firstRefRe })
    .getByRole("checkbox", { name: /Select adjustment / })
    .check();
  // a different line from the one the first page takes, so only the adjustment can conflict
  await stalePage
    .getByRole("table", { name: "Missed claim lines" })
    .getByRole("checkbox", { name: /^Select / })
    .nth(1)
    .check();

  await page.goto("/claims/missed");
  await page.getByLabel("Manufacturer").selectOption({ label: voltLabel });
  await page.getByRole("button", { name: "Apply filters" }).click();
  const adjCarryTable = page.getByRole("table", { name: "Adjustments to carry" });
  await expect(adjCarryTable).toBeVisible();
  const approvedAdjRow = adjCarryTable.getByRole("row", { name: firstRefRe });
  await expect(approvedAdjRow).toBeVisible();
  await approvedAdjRow.getByRole("checkbox", { name: /Select adjustment / }).check();
  await page
    .getByRole("table", { name: "Missed claim lines" })
    .getByRole("checkbox", { name: /^Select / })
    .first()
    .check();
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

  // Batch total is the server string (digit grouping only, no arithmetic)
  const res = await page.request.get(`/api/v1/claim-batches/${batchWithAdjId}`);
  expect(res.ok()).toBe(true);
  const serverTotal = ((await res.json()) as { total_amount: string }).total_amount;
  const totalsCard = page
    .getByRole("region")
    .filter({ has: page.getByText("Batch total (lines + carried adjustments, from server)") });
  await expect(totalsCard.getByText(grouping(serverTotal), { exact: true })).toBeVisible();

  // 14. adjustment_already_included: the stale page still has the adjustment selected
  await stalePage.getByRole("button", { name: /Create batch from selected/ }).click();
  await stalePage.getByRole("dialog").getByRole("button", { name: "Create batch" }).click();
  await expect(stalePage.getByRole("dialog")).toBeHidden();
  await expect(
    stalePage
      .getByRole("alert")
      .filter({ hasText: "Some of these adjustments are already in a claim batch." }),
  ).toBeVisible();
  const includedAlert = stalePage
    .getByRole("alert")
    .filter({ hasText: "The adjustment is already in a claim batch." });
  await expect(includedAlert).toBeVisible();
  await expect(includedAlert.getByRole("link", { name: "View batch" })).toHaveAttribute(
    "href",
    `/claims/batches/${batchWithAdjId}`,
  );
  await stalePage.close();

  // Cleanup contexts
  await approver1Ctx.close();
  await approver2Ctx.close();
  await analystCtx.close();
});
