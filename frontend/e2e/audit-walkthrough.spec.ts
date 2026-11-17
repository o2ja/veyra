import { readFileSync, writeFileSync } from "node:fs";

import { test, totp } from "./support";

// Manual audit, not CI: `AUDIT_WALK=1 npx playwright test audit-walkthrough`. Needs seed-credentials.local.json.
test.skip(!process.env.AUDIT_WALK, "manual audit, set AUDIT_WALK=1");

const ROUTES = [
  "/",
  "/dashboard",
  "/recovery",
  "/claims/missed",
  "/claims/batches",
  "/claims/adjustments",
  "/claims/templates",
  "/calculations",
  "/calculations/rebates",
  "/agreements",
  "/imports",
  "/imports/new",
  "/files",
  "/lines/sales",
  "/lines/purchases",
  "/master-data/manufacturers",
  "/master-data/customers",
  "/master-data/products",
  "/master-data/branches",
  "/master-data/uom",
  "/master-data/unmapped",
  "/reconciliation/new",
  "/reconciliation/reason-codes",
  "/extractions/usage",
  "/reports/audit-pack",
  "/audit",
  "/admin/users",
  "/admin/invitations",
  "/admin/settings",
  "/admin/jobs",
  "/account/security",
];

test("walk every static route as the admin", async ({ page }) => {
  const creds = JSON.parse(readFileSync("../seed-credentials.local.json", "utf8"));
  await page.goto("/login");
  await page.getByLabel("Email").fill(creds.adminEmail);
  await page.getByLabel("Password").fill(creds.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/login\/mfa/);
  await page.getByLabel(/code/i).fill(totp(creds.totpSecret));
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));

  let errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(`console: ${m.text().slice(0, 160)}`));
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message.slice(0, 160)}`));
  page.on(
    "response",
    (r) =>
      r.status() >= 400 && errors.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`),
  );

  const report: Record<string, unknown>[] = [];
  for (const size of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    for (const route of ROUTES) {
      errors = [];
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      const h1 =
        (await page
          .locator("h1")
          .first()
          .textContent({ timeout: 3000 })
          .catch(() => null)) ?? "(no h1)";
      const rows = await page.locator("tbody tr").count();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      report.push({ w: size.width, route, h1: h1.trim(), rows, overflow, errors: [...errors] });
    }
  }
  writeFileSync("../audit-walkthrough.local.json", JSON.stringify(report, null, 1));
});
