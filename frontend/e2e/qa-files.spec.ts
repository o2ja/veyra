import { expect } from "@playwright/test";

import { PASSWORD, enrolMfa, noViolations, signIn, test, tokenFromMail, uniqueEmail } from "./support";

test.describe("QA Files: uploads, file list, scan status polling, detail view, empty/error states, axe", () => {
  test("covers files upload, empty state, details, and axe accessibility", async ({ page }) => {
    test.setTimeout(180_000);
    const email = uniqueEmail("qa-files-admin");

    // 1. Register Admin
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Files Admin");
    await page.getByLabel("Company name").fill("QA Files Enterprise");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(email);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, email);
    await enrolMfa(page);

    // 2. Files Page: Empty state
    await page.goto("/files");
    await expect(page.getByRole("heading", { name: "Files" })).toBeVisible();
    await expect(page.getByText("No files yet.")).toBeVisible();
    await noViolations(page, "/files (empty)");

    // 3. Upload a file
    await page.setInputFiles('input[type="file"]', {
      name: "test-sales.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "document_number,line_number,product_code,quantity,unit_price\nINV-001,1,PROD-A,10,25.50\n",
      ),
    });
    await page.getByRole("button", { name: "Upload" }).click();
    await expect(page.getByText(/test-sales\.csv uploaded/)).toBeVisible();

    // 4. File appears in table
    const fileRow = page.getByRole("table").locator("tbody tr").first();
    await expect(fileRow).toBeVisible();
    await expect(fileRow.getByText("test-sales.csv")).toBeVisible();
    await noViolations(page, "/files (populated)");

    // 5. Navigate to file detail page
    await fileRow.getByRole("link", { name: "test-sales.csv" }).click();
    await expect(page).toHaveURL(/\/files\/[0-9a-f-]+/);
    await expect(page.getByRole("heading", { name: "test-sales.csv" })).toBeVisible();
    await expect(page.getByText("Status")).toBeVisible();
    await noViolations(page, "/files/:fileId");

    // 6. Error state: Not found file ID
    await page.goto("/files/00000000-0000-0000-0000-000000000000");
    await expect(page.getByRole("alert")).toBeVisible();
    await noViolations(page, "/files/00000000-0000-0000-0000-000000000000 (not found)");
  });
});
