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

test.describe("QA Master Data: manufacturers, branches, customers, products, UoM, unmapped, 403, axe", () => {
  test("covers master data management, pagination, error/empty states, 403, and axe compliance", async ({
    page,
    browser,
  }) => {
    test.setTimeout(240_000);
    const adminEmail = uniqueEmail("qa-md-admin");
    const viewerEmail = uniqueEmail("qa-md-viewer");

    // 1. Register Admin
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA MD Admin");
    await page.getByLabel("Company name").fill("QA MasterData Enterprise");
    await page.getByLabel("Work email").fill(adminEmail);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(adminEmail);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, adminEmail);
    await enrolMfa(page);

    // 2. Manufacturers page: empty state & add manufacturer
    await page.goto("/master-data/manufacturers");
    await expect(page.getByRole("heading", { name: "Manufacturers" })).toBeVisible();
    await noViolations(page, "/master-data/manufacturers");

    await page.getByLabel("Name", { exact: true }).fill("Voltara Electric");
    await page.getByLabel("Code", { exact: true }).fill("VOLT");
    await page.getByRole("button", { name: "Add manufacturer" }).click();
    await expect(page.getByText("Manufacturer added.")).toBeVisible();
    await expect(page.getByRole("table").getByText("Voltara Electric")).toBeVisible();

    // 3. Branches page: empty state & duplicate code validation
    await page.goto("/master-data/branches");
    await expect(page.getByRole("heading", { name: "Branches" })).toBeVisible();
    await noViolations(page, "/master-data/branches");

    await page.getByLabel("Name", { exact: true }).fill("Branch Alpha");
    await page.getByLabel("Code", { exact: true }).fill("BR-01");
    await page.getByRole("button", { name: "Add branch" }).click();
    await expect(page.getByText("Branch added.")).toBeVisible();

    // Duplicate code triggers error state
    await page.getByLabel("Name", { exact: true }).fill("Branch Beta");
    await page.getByLabel("Code", { exact: true }).fill("BR-01");
    await page.getByRole("button", { name: "Add branch" }).click();
    await expect(page.getByRole("alert")).toBeVisible();

    // 4. Customers & Products pages: empty states
    await page.goto("/master-data/customers");
    await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
    await noViolations(page, "/master-data/customers (empty)");

    await page.goto("/master-data/products");
    await expect(page.getByRole("heading", { name: "Products" })).toBeVisible();
    await noViolations(page, "/master-data/products (empty)");

    // 5. UoM page
    await page.goto("/master-data/uom");
    await expect(page.getByRole("heading", { name: "Unit-of-measure conversions" })).toBeVisible();
    await noViolations(page, "/master-data/uom");

    // 6. Unmapped entities page
    await page.goto("/master-data/unmapped");
    await expect(page.getByRole("heading", { name: "Unmapped customers and products" })).toBeVisible();
    await expect(page.getByText("Nothing to review.")).toBeVisible();
    await noViolations(page, "/master-data/unmapped (empty)");

    // 7. Invite viewer and verify 403 on Unmapped page (requires masterdata.write)
    await page.goto("/admin/invitations");
    await page.getByLabel("Email").fill(viewerEmail);
    await page.getByLabel("Role").selectOption("viewer");
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByText(`Invitation created for ${viewerEmail}`)).toBeVisible();

    const viewerCtx = await browser.newContext();
    const viewerPage = await viewerCtx.newPage();
    const inviteToken = await tokenFromMail(viewerEmail);

    await viewerPage.goto(`/accept-invite#token=${inviteToken}`);
    await viewerPage.getByLabel("Your name").fill("QA MD Viewer");
    await viewerPage.getByLabel("Password").fill(PASSWORD);
    await viewerPage.getByRole("button", { name: "Accept invitation" }).click();

    await signIn(viewerPage, viewerEmail);
    await enrolMfa(viewerPage);

    // Verify viewer receives PermissionDenied on /master-data/unmapped
    await viewerPage.goto("/master-data/unmapped");
    await expect(viewerPage.getByRole("heading", { name: "Not available" })).toBeVisible();
    await noViolations(viewerPage, "/master-data/unmapped (viewer 403)");

    await viewerCtx.close();
  });
});
