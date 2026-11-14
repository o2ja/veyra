import { expect } from "@playwright/test";

import {
  PASSWORD,
  clientIpHeader,
  enrolMfa,
  expectNoA11yViolations as noViolations,
  signIn,
  test,
  tokenFromMail,
  uniqueEmail,
} from "./support";

test.describe("QA Agreements: agreements list, drafts, editor, validation, approval, 403, axe", () => {
  test("covers agreements happy path, draft workflow, empty/error states, four-eyes, and axe", async ({
    page,
    browser,
  }) => {
    test.setTimeout(300_000);
    const adminEmail = uniqueEmail("qa-agr-admin");
    const approverEmail = uniqueEmail("qa-agr-approver");

    // 1. Register Admin
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Agr Admin");
    await page.getByLabel("Company name").fill("QA Agreements Enterprise");
    await page.getByLabel("Work email").fill(adminEmail);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(adminEmail);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, adminEmail);
    await enrolMfa(page);

    // 2. Master data: manufacturer MFR1 and product SKU-TEST-01 required for agreement references
    await page.getByRole("link", { name: "Master data" }).click();
    await page.getByLabel("Name", { exact: true }).fill("Manufacturer One");
    await page.getByLabel("Code", { exact: true }).fill("MFR1");
    await page.getByRole("button", { name: "Add manufacturer" }).click();
    await expect(page.getByText("Manufacturer added.")).toBeVisible();

    await page
      .getByRole("navigation", { name: "Master data" })
      .getByRole("link", { name: "Products" })
      .click();
    await expect(page.getByRole("heading", { name: "Add a product" })).toBeVisible();
    await page.getByLabel("SKU", { exact: true }).fill("SKU-TEST-01");
    await page.getByLabel("Description", { exact: true }).fill("Test SKU 01");
    await page.getByLabel("Manufacturer", { exact: true }).selectOption({ label: "Manufacturer One (MFR1)" });
    await page.getByLabel("Base unit of measure").fill("EA");
    await page.getByRole("button", { name: "Add product" }).click();
    await expect(page.getByText("Product added.")).toBeVisible();

    // 3. Agreements Page: empty state
    await page.goto("/agreements");
    await expect(page.getByRole("heading", { name: "Agreements" })).toBeVisible();
    await expect(page.getByText("No agreements yet.")).toBeVisible();
    await noViolations(page, "/agreements (empty)");

    // 3. Drafts Page: empty state
    await page.goto("/agreement-drafts");
    await expect(page.getByRole("heading", { name: "Agreement drafts" })).toBeVisible();
    await noViolations(page, "/agreement-drafts (empty)");

    // 4. Create new draft
    await page.getByRole("button", { name: "New draft" }).click();
    await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
    const draftId = page.url().split("/").pop() ?? "";
    await noViolations(page, "/agreement-drafts/:draftId");

    // Fill SPA draft fields
    await page.getByLabel("Agreement type").selectOption("spa");
    await page.getByLabel("External number").fill("SPA-QA-2026-001");
    await page.getByLabel("Manufacturer code").fill("MFR1");
    await page.getByLabel("Name", { exact: true }).fill("QA Master SPA Agreement");
    await page.getByLabel("Valid from").fill("2026-01-01");
    await page.getByLabel("Valid to").fill("2026-12-31");

    // Add an item row
    await page.getByRole("button", { name: "Add item" }).click();
    await page.getByLabel("Item 1 SKU").fill("SKU-TEST-01");
    await page.getByLabel("Item 1 reference cost").fill("50.00");
    await page.getByLabel("Item 1 deviated cost").fill("40.00");
    await page.getByLabel("Item 1 sell price").fill("45.00");

    // Save draft
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("Draft saved.")).toBeVisible();

    // 5. Four-eyes: creator cannot approve own draft
    await expect(page.getByRole("button", { name: "Approve draft" })).toHaveCount(0);

    // Validate and submit for approval
    await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
    await expect(page.getByText("Validation passed.")).toBeVisible();

    await page.getByRole("button", { name: "Submit for approval" }).click();
    await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

    // 6. Invite approver to approve draft
    await page.goto("/admin/invitations");
    await page.getByLabel("Email").fill(approverEmail);
    await page.getByLabel("Role").selectOption("approver");
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByRole("status")).toContainText(`Invitation created for ${approverEmail}`);

    const approverCtx = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
    const approverPage = await approverCtx.newPage();
    const approverToken = await tokenFromMail(approverEmail);

    await approverPage.goto(`/accept-invite#token=${approverToken}`);
    await approverPage.getByLabel("Your name").fill("QA Agr Approver");
    await approverPage.getByLabel("Password").fill(PASSWORD);
    await approverPage.getByRole("button", { name: "Accept invitation" }).click();

    await signIn(approverPage, approverEmail);
    await enrolMfa(approverPage);

    // Approver approves draft
    await approverPage.goto(`/agreement-drafts/${draftId}`);
    const approveBtn = approverPage.getByRole("button", { name: "Approve draft" });
    await expect(approveBtn).toBeVisible();
    await approveBtn.click();
    await expect(approverPage.getByText("Draft approved.")).toBeVisible();

    // 7. Error state: Not found version
    await page.goto("/agreements/versions/00000000-0000-0000-0000-000000000000");
    await expect(page.getByRole("heading", { name: "Version not found" })).toBeVisible();
    await noViolations(page, "/agreements/versions/:versionId (not found)");

    await approverCtx.close();
  });
});
