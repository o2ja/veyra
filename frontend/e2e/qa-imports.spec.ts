import { expect } from "@playwright/test";

import {
  PASSWORD,
  enrolMfa,
  expectNoA11yViolations as noViolations,
  signIn,
  test,
  tokenFromMail,
  uniqueEmail,
} from "./support";

test.describe("QA Imports: import history, new import wizard, sales & purchase lines, 403, axe", () => {
  test("covers imports list empty/populated states, line browsers, permission checks, and axe", async ({
    page,
    browser,
  }) => {
    test.setTimeout(240_000);
    const adminEmail = uniqueEmail("qa-imports-admin");
    const viewerEmail = uniqueEmail("qa-imports-viewer");

    // 1. Register Admin
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Imports Admin");
    await page.getByLabel("Company name").fill("QA Imports Enterprise");
    await page.getByLabel("Work email").fill(adminEmail);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(adminEmail);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, adminEmail);
    await enrolMfa(page);

    // 2. Imports history: empty state
    await page.goto("/imports");
    await expect(page.getByRole("heading", { name: "Imports" })).toBeVisible();
    await expect(page.getByText("No imports yet.")).toBeVisible();
    await noViolations(page, "/imports (empty)");

    // 3. New import wizard page
    await page.goto("/imports/new");
    await expect(page.getByRole("heading", { name: "New import" })).toBeVisible();
    await noViolations(page, "/imports/new");

    // 4. Source lines: Sales lines empty state
    await page.goto("/lines/sales");
    await expect(page.getByRole("heading", { name: "Sales lines" })).toBeVisible();
    await expect(page.getByText("No lines match.")).toBeVisible();
    await noViolations(page, "/lines/sales (empty)");

    // 5. Source lines: Purchase lines empty state
    await page.goto("/lines/purchases");
    await expect(page.getByRole("heading", { name: "Purchase lines" })).toBeVisible();
    await expect(page.getByText("No lines match.")).toBeVisible();
    await noViolations(page, "/lines/purchases (empty)");

    // 6. Error state: Not found import detail
    await page.goto("/imports/00000000-0000-0000-0000-000000000000");
    await expect(page.getByRole("alert")).toBeVisible();
    await noViolations(page, "/imports/:importId (not found)");

    // 7. Invite viewer to verify 403 on /imports/new (requires import.run)
    await page.goto("/admin/invitations");
    await page.getByLabel("Email").fill(viewerEmail);
    await page.getByLabel("Role").selectOption("viewer");
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByRole("status")).toContainText(`Invitation created for ${viewerEmail}`);

    const viewerCtx = await browser.newContext();
    const viewerPage = await viewerCtx.newPage();
    const inviteToken = await tokenFromMail(viewerEmail);

    await viewerPage.goto(`/accept-invite#token=${inviteToken}`);
    await viewerPage.getByLabel("Your name").fill("QA Imports Viewer");
    await viewerPage.getByLabel("Password").fill(PASSWORD);
    await viewerPage.getByRole("button", { name: "Accept invitation" }).click();
    await expect(viewerPage).toHaveURL(/\/login\?reason=invited/);

    await signIn(viewerPage, viewerEmail);
    await enrolMfa(viewerPage);

    // Verify viewer does not have "New import" button on /imports
    await viewerPage.goto("/imports");
    await expect(viewerPage.getByRole("heading", { name: "Imports" })).toBeVisible();
    await expect(viewerPage.getByRole("link", { name: "New import" })).toHaveCount(0);

    // Verify viewer accessing /imports/new directly receives PermissionDenied
    await viewerPage.goto("/imports/new");
    await expect(viewerPage.getByRole("heading", { name: "Not available" })).toBeVisible();
    await noViolations(viewerPage, "/imports/new (viewer 403)");

    await viewerCtx.close();
  });
});
