import { expect } from "@playwright/test";

import { PASSWORD, enrolMfa, noViolations, signIn, test, tokenFromMail, uniqueEmail } from "./support";

test.describe("QA Auth: registration, verification, login, MFA, password reset, security, axe", () => {
  test("covers auth happy path, validation errors, invalid link, and accessibility", async ({ page }) => {
    test.setTimeout(180_000);
    const email = uniqueEmail("qa-auth-user");

    // 1. Accessibility on unauthenticated pages
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await noViolations(page, "/login");

    await page.goto("/register");
    await expect(page.getByRole("heading", { name: "Create your workspace" })).toBeVisible();
    await noViolations(page, "/register");

    await page.goto("/forgot-password");
    await expect(page.getByRole("heading", { name: "Reset your password" })).toBeVisible();
    await noViolations(page, "/forgot-password");

    // Invalid link state (no fragment token)
    await page.goto("/verify-email");
    await expect(page.getByRole("heading", { name: "Link not valid" })).toBeVisible();
    await noViolations(page, "/verify-email (invalid)");

    // 2. Client validation & error states on Login
    await page.goto("/login");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Enter a valid email address.")).toBeVisible();

    await signIn(page, "invalid-email@example.test", "wrong-password");
    await expect(page.getByRole("alert")).toBeVisible();

    // 3. Register happy path
    await page.goto("/register");
    await page.getByLabel("Your name").fill("QA Auth Admin");
    await page.getByLabel("Company name").fill("QA Auth Corp");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();

    // 4. Verify email link
    const verifyToken = await tokenFromMail(email);
    await page.goto(`/verify-email#token=${verifyToken}`);
    await expect(page.getByRole("heading", { name: "Confirm your email" })).toBeVisible();
    await noViolations(page, "/verify-email (valid)");

    // Incorrect password error on verify
    await page.getByLabel("Password").fill("wrong-password-here");
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();
    await expect(page.getByRole("alert")).toBeVisible();

    // Correct password succeeds
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();
    await expect(page).toHaveURL(/login\?reason=verified/);
    await expect(page.getByText("Your email is confirmed.")).toBeVisible();

    // 5. Sign in & MFA setup
    await signIn(page, email);
    await expect(page).toHaveURL("/mfa/setup");
    await noViolations(page, "/mfa/setup");

    await enrolMfa(page);
    await expect(page).toHaveURL("/");

    // 6. Security page
    await page.goto("/security");
    await expect(page.getByRole("heading", { name: "Password" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Recovery codes" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sign-in email" })).toBeVisible();
    await noViolations(page, "/security");
  });
});
