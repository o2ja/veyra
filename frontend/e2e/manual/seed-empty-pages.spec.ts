import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, type ConsoleMessage, type Page, type Response } from "@playwright/test";

import { apiJson, generated, importFile, inviteMember, test, totp, uniqueEmail } from "../support";

// Manual seed, not CI: `$env:E2E_MANUAL="1"; $env:SEED_EMPTY="1"; npx playwright test seed-empty-pages`. Needs seed-credentials.local.json.
test.skip(!process.env.SEED_EMPTY, "manual seed");

const creds = JSON.parse(
  readFileSync(new URL("../../../seed-credentials.local.json", import.meta.url), "utf8"),
) as { adminEmail: string; password: string; totpSecret: string };

interface Report {
  route: string;
  rows: number;
  errors: string[];
}

const reports: Report[] = [];

/** Visit a route as the admin, fail on any console error, page error or response >= 400 during the visit. */
async function visit(page: Page, route: string, texts: string[] = []): Promise<void> {
  const errors: string[] = [];
  const onConsole = (m: ConsoleMessage) => {
    if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 160)}`);
  };
  const onPageError = (e: Error) => errors.push(`pageerror: ${e.message.slice(0, 160)}`);
  const onResponse = (r: Response) => {
    if (r.status() >= 400) errors.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
  };
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  page.on("response", onResponse);
  try {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading").first()).toBeVisible();
    await expect.poll(() => page.locator("tbody tr").count(), { timeout: 15_000 }).toBeGreaterThan(0);
    for (const text of texts) await expect(page.getByText(text).first()).toBeVisible();
  } finally {
    page.off("console", onConsole);
    page.off("pageerror", onPageError);
    page.off("response", onResponse);
  }
  reports.push({ route, rows: await page.locator("tbody tr").count(), errors });
  expect(errors, `${route}: console/API errors`).toEqual([]);
}

async function hasRows(page: Page, route: string): Promise<boolean> {
  await page.goto(route);
  await page.waitForLoadState("networkidle");
  return (await page.locator("tbody tr").count()) > 0;
}

/** importFile for a file outside testdata (same wizard steps as support.ts). */
async function importLocalCsv(page: Page, type: string, absPath: string): Promise<void> {
  await page.goto("/imports/new");
  await page.getByLabel("File to upload").setInputFiles(absPath);
  await page.getByLabel("What the file contains").selectOption({ label: type });
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page).toHaveURL(/\/imports\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  const map = page.getByRole("heading", { name: "Map the columns" });
  const review = page.getByRole("heading", { name: "Check and confirm" });
  await expect(map.or(review)).toBeVisible({ timeout: 120_000 });
  if (await map.isVisible()) {
    await page.getByRole("button", { name: "Apply mapping and check rows" }).click();
    await expect(review).toBeVisible({ timeout: 60_000 });
  }
  // Unknown customer/SKU rows are rejected at check time and sent to the unmapped queue; with no valid rows
  // Confirm stays disabled, which is the intended outcome here.
  const confirm = page.getByRole("button", { name: "Confirm import" });
  if (await confirm.isEnabled()) {
    await confirm.click();
    await expect(page.getByRole("heading", { name: "Result" })).toBeVisible({ timeout: 120_000 });
  }
}

/** Start a calculation run from the UI and wait for the new run (found by id) to complete. */
async function runCalculation(page: Page): Promise<string> {
  type Runs = { data: { id: string; status: string }[] };
  const before = new Set(
    (await apiJson<Runs>(page, "GET", "/api/v1/calculation-runs?limit=50")).data.map((r) => r.id),
  );
  await page.goto("/calculations");
  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();
  let runId = "";
  await expect
    .poll(
      async () => {
        const runs = await apiJson<Runs>(page, "GET", "/api/v1/calculation-runs?limit=50");
        const run = runs.data.find((r) => !before.has(r.id));
        if (run?.status === "completed") runId = run.id;
        return run?.status ?? "";
      },
      { timeout: 180_000, intervals: [1_000] },
    )
    .toBe("completed");
  return runId;
}

test("seed the empty pages through the real UI and check them", async ({ page, browser }) => {
  test.setTimeout(900_000);

  await page.goto("/login");
  await page.getByLabel("Email").fill(creds.adminEmail);
  await page.getByLabel("Password").fill(creds.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/login\/mfa/);
  await page.getByLabel(/code/i).fill(totp(creds.totpSecret));
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));

  const approver = await inviteMember(
    page,
    browser,
    uniqueEmail("e2e-audit-approver"),
    "approver",
    "Audit Approver",
  );
  try {
    // 1. Purchase lines
    // Re-running is safe: a file already uploaded is refused ("already uploaded"), so import only empty pages.
    if (!(await hasRows(page, "/lines/purchases"))) {
      await importFile(page, "Purchases", "purchases/purchases_VOLT_2026.csv");
    }
    await visit(page, "/lines/purchases");

    // 2. Unmapped items: the HVAC file maps cleanly, so import a synthetic row with unknown codes instead
    if (!(await hasRows(page, "/master-data/unmapped"))) {
      const header = readFileSync(generated("sales/sales_scenarios_layoutA.csv"), "utf8").split(/\r?\n/)[0];
      const unmappedPath = join(tmpdir(), "veyra-unmapped-audit.csv");
      writeFileSync(
        unmappedPath,
        `${header}\nINV-AUDIT-900001,1,2026-06-10,2026-06-10,UNMAP01,Unmapped Customer (SYNTHETIC),BR01,ZZ-UNMAPPED-900,Unmapped test item,10,EA,50.00,40.00,500.00,,,INV,,\n`,
      );
      await importLocalCsv(page, "Sales", unmappedPath);
    }
    await visit(page, "/master-data/unmapped");

    // 3. Rebate tracker: approved REB-VOLT-2026, then a new calculation run
    await page.goto("/agreement-drafts");
    await page.getByRole("button", { name: "New draft" }).click();
    await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
    const rebDraftId = page.url().split("/").pop() ?? "";
    await page.getByLabel("Agreement type").selectOption("rebate");
    await page.getByLabel("External number").fill("REB-VOLT-2026");
    await page.getByLabel("Manufacturer code").fill("VOLT");
    await page.getByLabel("Name", { exact: true }).fill("Voltara annual volume rebate 2026 (SYNTHETIC)");
    await page.getByLabel("Valid from").fill("2026-01-01");
    await page.getByLabel("Valid to").fill("2026-12-31");
    await page.getByLabel("Volume basis").selectOption("sales");
    await page.getByLabel("Tier metric").selectOption("amount");
    await page.getByLabel("Tier mode").selectOption("retroactive");
    await page.getByLabel("SPA lines").selectOption("include");
    await page.getByLabel("Product scope").selectOption("all");
    await page.getByLabel("Measurement period kind").fill("calendar_year");
    await page.getByLabel("Measurement period start").fill("2026-01-01");
    await page.getByLabel("Measurement period end").fill("2026-12-31");
    for (const [i, tier] of [
      { bound: "0", rate: "0.015" },
      { bound: "1000000", rate: "0.025" },
      { bound: "2000000", rate: "0.035" },
    ].entries()) {
      await page.getByRole("button", { name: "Add tier" }).click();
      await page.getByLabel("Lower bound").nth(i).fill(tier.bound);
      await page.getByLabel("Rate").nth(i).fill(tier.rate);
    }
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("Draft saved.")).toBeVisible();
    await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
    await expect(page.getByText("Validation passed.")).toBeVisible();
    await page.getByRole("button", { name: "Submit for approval" }).click();
    await expect(page.getByText("Draft submitted for approval.")).toBeVisible();
    await approver.page.goto(`/agreement-drafts/${rebDraftId}`);
    await approver.page.getByRole("button", { name: "Approve draft" }).click();
    await expect(approver.page.getByText("Draft approved.")).toBeVisible();
    await runCalculation(page);
    await visit(page, "/calculations/rebates");

    // 4. Export template
    await page.goto("/claims/templates");
    await page.getByRole("button", { name: "New template" }).click();
    await page.getByLabel("Template name").fill("Audit VOLT Template (SYNTHETIC)");
    const manufacturerSelect = page
      .getByRole("region", { name: "Create new export template" })
      .getByLabel("Manufacturer");
    const voltValue = await manufacturerSelect
      .locator("option")
      .filter({ hasText: "(VOLT)" })
      .first()
      .getAttribute("value");
    if (!voltValue) throw new Error("VOLT option missing in template manufacturer select");
    await manufacturerSelect.selectOption(voltValue);
    await page.getByRole("button", { name: "Save template" }).click();
    await visit(page, "/claims/templates", ["Audit VOLT Template (SYNTHETIC)"]);

    // 5. Claim adjustments: re-import corrected sales, new run, approver proposes
    await importFile(page, "Sales", "sales/sales_corrected_changed_rows.csv");
    const runId = await runCalculation(page);
    await approver.page.goto(`/calculations/${runId}`);
    await approver.page.getByRole("button", { name: "Propose adjustments" }).click();
    await expect(approver.page.getByText(/adjustment\(s\)\s+proposed/)).toBeVisible({ timeout: 30_000 });
    await visit(page, "/claims/adjustments");

    // 6. Reason-code mappings (the page only loads with a manufacturer_id in the URL)
    const manufacturers = await apiJson<{ data: { id: string; code: string }[] }>(
      page,
      "GET",
      "/api/v1/manufacturers?limit=200",
    );
    const volt = manufacturers.data.find((m) => m.code === "VOLT");
    if (!volt) throw new Error("VOLT manufacturer missing");
    await visit(page, `/reconciliation/reason-codes?manufacturer_id=${volt.id}`);
    const codes = page.locator('input[aria-label^="External code row"]');
    await expect
      .poll(async () => await codes.evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value)))
      .toEqual(expect.arrayContaining(["E07", "E12"]));
  } finally {
    await approver.context.close();
  }

  console.log(JSON.stringify(reports, null, 2));
});
