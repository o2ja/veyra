import { expect } from "@playwright/test";

import { PASSWORD, enrolMfa, expectNoA11yViolations as noViolations, signIn, test, tokenFromMail, uniqueEmail } from "./support";

test.describe("QA Home: dashboard, workspace info, session details, axe", () => {
  test("displays workspace and role details for signed-in user and passes axe", async ({ page }) => {
    test.setTimeout(180_000);
    const email = uniqueEmail("qa-home-user");

    // Register & sign in
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Home User");
    await page.getByLabel("Company name").fill("QA Home Enterprise");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();

    const token = await tokenFromMail(email);
    await page.goto(`/verify-email#token=${token}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();

    await signIn(page, email);
    await enrolMfa(page);

    // Verify Home page
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "QA Home Enterprise" })).toBeVisible();
    await expect(page.getByText(`Signed in as ${email}`)).toBeVisible();
    await expect(page.getByText("(admin)")).toBeVisible();
    await noViolations(page, "/ (home)");
  });
});
