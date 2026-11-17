import { readFileSync } from "node:fs";

import { expect } from "@playwright/test";

import { test, totp } from "../support";

// Manual, not CI: `E2E_MANUAL=1 SEED_FAILED=1 npx playwright test seed-failed-job`. Expects one failed job row in the tenant
// (injected by SQL: uploads are validated, so no real failure is reachable); /extractions/usage renders empty (AI off).
test.skip(!process.env.SEED_FAILED, "manual seed, set SEED_FAILED=1");

test("a real failed job appears on the failed-jobs page; AI usage page renders", async ({ page }) => {
  const c = JSON.parse(readFileSync("../seed-credentials.local.json", "utf8"));
  await page.goto("/login");
  await page.getByLabel("Email").fill(c.adminEmail);
  await page.getByLabel("Password").fill(c.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/login\/mfa/);
  await page.getByLabel(/code/i).fill(totp(c.totpSecret));
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));

  const bad: string[] = [];
  page.on("console", (m) => m.type() === "error" && bad.push(`console: ${m.text().slice(0, 120)}`));
  page.on("response", (r) => r.status() >= 400 && bad.push(`${r.status()} ${new URL(r.url()).pathname}`));
  await expect(async () => {
    await page.goto("/admin/jobs");
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 90_000, intervals: [3_000] });
  await page.goto("/extractions/usage");
  await expect(page.getByRole("heading", { name: "AI usage" })).toBeVisible();
  expect(bad, bad.join(" | ")).toEqual([]);
});
