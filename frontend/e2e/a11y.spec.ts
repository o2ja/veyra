import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, tokenFromMail, uniqueEmail } from "./support";

// WCAG 2.1 A/AA automated checks (NFR-09) on every page S1 added. Automated checks find only part of
// the issues; manual keyboard use was spot-checked during the S1 browser walkthrough.
async function noViolations(page: Page, name: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = result.violations.map((v) => `${v.id} (${v.nodes.length})`);
  expect(summary, `${name}: ${summary.join(", ")}`).toEqual([]);
}

test("S1 pages have no automated WCAG 2.1 AA violations", async ({ page }) => {
  for (const path of ["/login", "/register", "/forgot-password"]) {
    await page.goto(path);
    await expect(page.getByRole("heading").first()).toBeVisible();
    await noViolations(page, path);
  }

  const admin = uniqueEmail("a11y-admin");
  await page.goto("/register");
  await page.getByLabel("Your name").fill("A11y Admin");
  await page.getByLabel("Company name").fill("A11y Co (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await page.getByRole("button", { name: "Start setup" }).click();
  await expect(page.getByRole("img", { name: /QR code/ })).toBeVisible();
  await noViolations(page, "/mfa/setup");
  await page.goto("/mfa/setup");
  await enrolMfa(page);

  for (const path of [
    "/",
    "/admin/users",
    "/admin/invitations",
    "/admin/settings",
    "/audit",
    "/account/security",
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading").first()).toBeVisible();
    await noViolations(page, path);
  }
});
