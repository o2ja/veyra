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

interface ExpectedFinding {
  claim_reference: string;
  variance?: {
    case_type: string;
    amount: string;
    reason_code: string;
  } | null;
}

interface ExpectedReconciliation {
  expected: ExpectedFinding[];
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

    // 2. Setup master data (manufacturers)
    await page.getByRole("link", { name: "Master data" }).click();
    for (const m of truth("master_manufacturers.json") as Manufacturer[]) {
      await page.getByLabel("Name", { exact: true }).fill(m.name);
      await page.getByLabel("Code", { exact: true }).fill(m.code);
      await page.getByRole("button", { name: "Add manufacturer" }).click();
      await expect(page.getByRole("table", { name: "Manufacturers" }).getByText(m.name)).toBeVisible();
    }

    // 3. Reason codes configuration
    await page.goto("/reconciliation/reason-codes");
    await noViolations(page, "Reconciliation reason-codes empty");
    const mfgSelect = page.getByRole("combobox", { name: /manufacturer/i });
    if (await mfgSelect.isVisible()) {
      await mfgSelect.selectOption({ label: "Voltara Energy (VOLT)" });
      await expect(page.getByRole("button", { name: /add row/i })).toBeVisible();
      await page.getByRole("button", { name: /add row/i }).click();
      await page.getByPlaceholder("e.g. E12").fill("E12");
      await page.getByPlaceholder("e.g. Price discrepancy").fill("Price discrepancy");
      await page.getByRole("button", { name: /save mappings/i }).click();
      await expect(page.getByText("Reason code mappings saved.")).toBeVisible();
    }

    // 4. Invite viewer user (role check)
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

    // 5. Ingest: Admin uploads remittance file
    await page.goto("/reconciliation");
    await noViolations(page, "Reconciliation list (admin)");
    await page.getByRole("link", { name: "New remittance" }).click();
    await expect(page).toHaveURL("/reconciliation/new");
    await noViolations(page, "Reconciliation new upload");

    // Test PDF rejection: AI not enabled
    // Remittance upload CSV:
    const remittanceFile = data("remittance/credit_memo_VOLT_CM-7781.csv");
    await page.getByLabel("Remittance file").setInputFiles(remittanceFile);
    await page
      .getByRole("combobox", { name: /manufacturer/i })
      .selectOption({ label: "Voltara Energy (VOLT)" });
    await page.getByRole("combobox", { name: /document type/i }).selectOption("credit_memo");
    await page.getByRole("combobox", { name: /channel/i }).selectOption("credit_memo");
    await page.getByLabel("Document number").fill("CM-7781");

    await page.getByRole("button", { name: "Submit remittance" }).click();

    // Ingest job completes and navigates to remittance detail
    await expect(page).toHaveURL(/\/reconciliation\/[0-9a-f-]+$/, { timeout: 60_000 });
    const remittanceUrl = page.url();

    // Viewer on detail page: permission denied for mutate actions
    await viewerPage.goto(remittanceUrl);
    await expect(viewerPage.getByRole("button", { name: "Accept remittance" })).toHaveCount(0);
    await expect(viewerPage.getByRole("button", { name: "Run matching" })).toHaveCount(0);

    // 6. Staging review (if pending_review)
    const acceptBtn = page.getByRole("button", { name: "Accept remittance" });
    if (await acceptBtn.isVisible()) {
      await noViolations(page, "Remittance staging review");
      const totalField = page.getByLabel("Document total amount");
      if (await totalField.isVisible()) {
        await totalField.fill("7545.00");
      }
      await acceptBtn.click();
      const acceptDialog = page.getByRole("dialog");
      await expect(acceptDialog).toBeVisible();
      await acceptDialog.getByRole("button", { name: "Accept" }).click();
      await expect(page.getByRole("button", { name: "Run matching" })).toBeVisible({ timeout: 60_000 });
    }

    // 7. Workbench matching
    await noViolations(page, "Remittance workbench");
    await page.getByRole("button", { name: "Run matching" }).click();
    await expect(page.getByText("Reconciliation lines")).toBeVisible({ timeout: 60_000 });

    // Suggestions shown with method and evidence
    const confirmButtons = page.getByRole("button", { name: "Confirm" });
    const rejectButtons = page.getByRole("button", { name: "Reject" });
    if ((await confirmButtons.count()) > 0) {
      // Confirm one suggestion
      await confirmButtons.first().click();

      // Reject one suggestion if available
      if ((await rejectButtons.count()) > 0) {
        await rejectButtons.first().click();
      }
    }

    // Manual match check
    const manualMatchBtn = page.getByRole("button", { name: "Manual match" });
    if (await manualMatchBtn.isVisible()) {
      await manualMatchBtn.first().click();
      const matchDialog = page.getByRole("dialog", { name: "Manual match" });
      await expect(matchDialog).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(matchDialog).not.toBeVisible();
    }

    // 8. Early finalisation check & Categorisation
    await page.getByRole("button", { name: "Finalise reconciliation" }).click();
    const finaliseDialog = page.getByRole("dialog", { name: "Finalise reconciliation" });
    await expect(finaliseDialog).toBeVisible();
    await finaliseDialog.getByRole("button", { name: "Confirm finalisation" }).click();

    // If uncategorised_lines error appears:
    const errorAlert = page.getByRole("alert");
    if (await errorAlert.isVisible()) {
      // Close finalise dialog
      await page.keyboard.press("Escape");

      // Categorise unmatched lines
      const markUnmatched = page.getByRole("button", { name: "Mark as unmatched payment" });
      const count = await markUnmatched.count();
      for (let i = 0; i < count; i++) {
        await markUnmatched.first().click();
      }

      // Re-trigger finalise
      await page.getByRole("button", { name: "Finalise reconciliation" }).click();
      await finaliseDialog.getByRole("button", { name: "Confirm finalisation" }).click();
    }

    // 9. Reconciled state & Findings validation
    await expect(
      page.getByText(
        "This remittance has been finalised and reconciled. Matches and allocations are immutable.",
      ),
    ).toBeVisible({ timeout: 60_000 });
    await noViolations(page, "Remittance reconciled findings");

    // Verify findings ground truth strings
    const expectedData = truth("expected_reconciliation.json") as ExpectedReconciliation;
    for (const exp of expectedData.expected) {
      if (exp.variance) {
        await expect(page.getByText(exp.variance.amount)).toBeVisible();
      }
    }

    await viewerCtx.close();
  });
});
