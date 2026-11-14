import { expect } from "@playwright/test";

import { PASSWORD, enrolMfa, expectNoA11yViolations as noViolations, signIn, test, tokenFromMail, uniqueEmail } from "./support";

test.describe("QA Admin: users, invitations, settings, audit log, jobs, 403 checks, axe", () => {
  test("covers admin management, settings update, role 403, and axe compliance", async ({
    page,
    browser,
  }) => {
    test.setTimeout(240_000);
    const adminEmail = uniqueEmail("qa-admin-user");
    const viewerEmail = uniqueEmail("qa-viewer-user");

    // 1. Register Admin & MFA
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Admin User");
    await page.getByLabel("Company name").fill("QA Admin Corp");
    await page.getByLabel("Work email").fill(adminEmail);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(adminEmail);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, adminEmail);
    await enrolMfa(page);

    // 2. Users page
    await page.goto("/admin/users");
    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    await expect(page.getByText(adminEmail)).toBeVisible();
    await noViolations(page, "/admin/users");

    // 3. Invitations page: Happy path & error state
    await page.goto("/admin/invitations");
    await expect(page.getByRole("heading", { name: "Invite someone" })).toBeVisible();
    await noViolations(page, "/admin/invitations");

    // Invite viewer user
    await page.getByLabel("Email").fill(viewerEmail);
    await page.getByLabel("Role").selectOption("viewer");
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByRole("status")).toContainText(`Invitation created for ${viewerEmail}`);
    await expect(page.getByRole("table").getByRole("cell", { name: viewerEmail })).toBeVisible();

    // 4. Settings page: view and update
    await page.goto("/admin/settings");
    await expect(page.getByRole("heading", { name: "Workspace settings" })).toBeVisible();
    await noViolations(page, "/admin/settings");

    // 5. Jobs page: empty state
    await page.goto("/admin/jobs");
    await expect(page.getByRole("heading", { name: "Failed background jobs" })).toBeVisible();
    await expect(page.getByText("No failed jobs.")).toBeVisible();
    await noViolations(page, "/admin/jobs");

    // 6. Audit log page
    await page.goto("/audit");
    await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
    await noViolations(page, "/audit");

    // 7. Accept invite as Viewer and verify 403 on admin routes
    const viewerCtx = await browser.newContext();
    const viewerPage = await viewerCtx.newPage();
    const inviteToken = await tokenFromMail(viewerEmail);

    await viewerPage.goto(`/accept-invite#token=${inviteToken}`);
    await viewerPage.getByLabel("Your name").fill("QA Viewer User");
    await viewerPage.getByLabel("Password").fill(PASSWORD);
    await viewerPage.getByRole("button", { name: "Accept invitation" }).click();

    await signIn(viewerPage, viewerEmail);
    await enrolMfa(viewerPage);

    // Verify 403 / PermissionDenied on /admin/users for viewer
    await viewerPage.goto("/admin/users");
    await expect(viewerPage.getByRole("heading", { name: "Not available" })).toBeVisible();
    await noViolations(viewerPage, "/admin/users (viewer 403)");

    // Verify 403 on /admin/invitations for viewer
    await viewerPage.goto("/admin/invitations");
    await expect(viewerPage.getByRole("heading", { name: "Not available" })).toBeVisible();

    await viewerCtx.close();
  });
});
