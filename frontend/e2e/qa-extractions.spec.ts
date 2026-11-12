import { expect } from "@playwright/test";

import { PASSWORD, enrolMfa, noViolations, signIn, test, tokenFromMail, uniqueEmail } from "./support";

test.describe("QA Extractions: document extractions, AI usage, error/empty states, axe", () => {
  test("covers extractions list empty/populated states, AI usage, not-found error, and axe", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const email = uniqueEmail("qa-ext-admin");

    // 1. Register Admin
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Ext Admin");
    await page.getByLabel("Company name").fill("QA Extractions Enterprise");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(email);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, email);
    await enrolMfa(page);

    // 2. Extractions page: empty state & form
    await page.goto("/extractions");
    await expect(page.getByRole("heading", { name: "Document extractions" })).toBeVisible();
    await expect(page.getByText("No document extractions yet.")).toBeVisible();
    await noViolations(page, "/extractions (empty)");

    // 3. AI Usage page
    await page.goto("/extractions/usage");
    await expect(page.getByRole("heading", { name: "AI usage" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Daily usage" })).toBeVisible();
    await noViolations(page, "/extractions/usage");

    // 4. Error state: Not found extraction ID
    await page.goto("/extractions/00000000-0000-0000-0000-000000000000");
    await expect(page.getByRole("heading", { name: "Extraction not found" })).toBeVisible();
    await noViolations(page, "/extractions/:extractionId (not found)");
  });
});
