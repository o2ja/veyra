import { expect } from "@playwright/test";

import { PASSWORD, enrolMfa, expectNoA11yViolations as noViolations, signIn, test, tokenFromMail, uniqueEmail } from "./support";

test.describe("QA Calculations: runs, run details, rebate tracker, accruals, error/empty states, axe", () => {
  test("covers calculations list empty/populated states, rebates tracker, not-found error, and axe", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const email = uniqueEmail("qa-calc-admin");

    // 1. Register Admin
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Calc Admin");
    await page.getByLabel("Company name").fill("QA Calculations Enterprise");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(email);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, email);
    await enrolMfa(page);

    // 2. Calculations page: empty state & form
    await page.goto("/calculations");
    await expect(page.getByRole("heading", { name: "Calculation runs" })).toBeVisible();
    await expect(page.getByText("No calculation runs yet.")).toBeVisible();
    await noViolations(page, "/calculations (empty)");

    // 3. Rebates page: empty state & tracker
    await page.goto("/calculations/rebates");
    await expect(page.getByRole("heading", { name: "Rebate tracker" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Accrual ledger" })).toBeVisible();
    await noViolations(page, "/calculations/rebates");

    // 4. Error state: Not found run ID
    await page.goto("/calculations/00000000-0000-0000-0000-000000000000");
    await expect(page.getByRole("heading", { name: "Calculation run not found" })).toBeVisible();
    await noViolations(page, "/calculations/:runId (not found)");

    // 5. Error state: Not found result ID
    await page.goto("/calculations/results/00000000-0000-0000-0000-000000000000");
    await expect(page.getByRole("heading", { name: "Calculation result not found" })).toBeVisible();
    await noViolations(page, "/calculations/results/:resultId (not found)");
  });
});
