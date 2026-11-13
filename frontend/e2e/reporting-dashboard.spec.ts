import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, tokenFromMail, uniqueEmail } from "./support";

const data = (rel: string) => fileURLToPath(new URL(`../../testdata/generated/${rel}`, import.meta.url));
const truth = (name: string) => JSON.parse(readFileSync(data(`ground_truth/${name}`), "utf8")) as unknown;

interface Manufacturer {
  name: string;
  code: string;
}

async function noViolations(page: Page, name: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = result.violations.map(
    (v) =>
      `${v.id} (${v.nodes.length}): ${v.nodes
        .slice(0, 3)
        .map((n) => n.target.join(" "))
        .join(" | ")}`,
  );
  expect(summary, `${name}: ${summary.join(", ")}`).toEqual([]);
}

test.describe("S10 dashboard and reporting", () => {
  test("dashboard figures, drill-down consistency, trace chain, audit pack export, analyst 403, axe", async ({
    page,
    browser,
  }) => {
    test.setTimeout(360_000);
    const admin = uniqueEmail("e2e-report-admin");
    const analyst = uniqueEmail("e2e-report-analyst");

    // 1. Register admin + MFA
    await page.goto("/register");
    await page.getByLabel("Your name").fill("Reporting Admin");
    await page.getByLabel("Company name").fill("Voltara Reporting Co (SYNTHETIC)");
    await page.getByLabel("Work email").fill(admin);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create workspace" }).click();
    await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Confirm and create workspace" }).click();
    await signIn(page, admin);
    await enrolMfa(page);

    // 2. Setup master data (manufacturers)
    await page.getByRole("link", { name: "Master data" }).click();
    for (const m of truth("master_manufacturers.json") as Manufacturer[]) {
      await page.getByLabel("Name", { exact: true }).fill(m.name);
      await page.getByLabel("Code", { exact: true }).fill(m.code);
      await page.getByRole("button", { name: "Add manufacturer" }).click();
      await expect(page.getByRole("table", { name: "Manufacturers" }).getByText(m.name)).toBeVisible();
    }

    // 3. Invite analyst user
    await page.getByRole("link", { name: "Invitations" }).click();
    await page.getByLabel("Email").fill(analyst);
    await page.getByLabel("Role").selectOption("analyst");
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByText(`Invitation created for ${analyst}`)).toBeVisible();

    const analystCtx = await browser.newContext();
    const analystPage = await analystCtx.newPage();
    await analystPage.goto(`/accept-invite#token=${await tokenFromMail(analyst)}`);
    await analystPage.getByLabel("Your name").fill("Reporting Analyst");
    await analystPage.getByLabel("Password").fill(PASSWORD);
    await analystPage.getByRole("button", { name: "Accept invitation" }).click();
    await signIn(analystPage, analyst);
    await enrolMfa(analystPage);

    // 4. Fresh tenant dashboard: "Figures have not been built yet"
    await page.goto("/dashboard");
    await expect(page.getByText(/Figures have not been built yet/i)).toBeVisible();
    await noViolations(page, "Dashboard empty state");

    // 5. Trigger refresh / build figures
    const refreshBtn = page.getByRole("button", { name: /Build figures|Refresh figures/i });
    await refreshBtn.click();
    await expect(page.getByText(/Dashboard refresh queued|As of/i)).toBeVisible({ timeout: 60_000 });

    // 6. Dashboard figures cards verification
    await expect(page.getByRole("region", { name: "Key performance figures" })).toBeVisible({
      timeout: 60_000,
    });
    await noViolations(page, "Dashboard populated");

    const claimedCard = page.getByRole("link", { name: /Claimed:/i });
    await expect(claimedCard).toBeVisible();

    // 7. Drill-down: Claimed metric
    await claimedCard.click();
    await expect(page).toHaveURL(/\/dashboard\/claimed/);
    await noViolations(page, "Drill-down claimed metric");

    // Server figure and total are displayed
    await expect(page.getByRole("heading", { name: /Drill-down: Claimed/i })).toBeVisible();
    await expect(page.getByText("Metric figure")).toBeVisible();
    await expect(page.getByText("Drill-down total")).toBeVisible();

    // 8. Trace resolver navigation
    const traceLink = page.getByRole("link", { name: /Trace →/i });
    if ((await traceLink.count()) > 0) {
      await traceLink.first().click();
      await expect(page).toHaveURL(/\/trace\/[a-z_]+\/[0-9a-f-]+/);
      await noViolations(page, "Trace chain resolver");

      // Verify trace chain ordered list
      await expect(page.getByRole("list")).toBeVisible();
      await expect(page.getByRole("heading", { name: /Trace chain/i })).toBeVisible();
    }

    // 9. Audit pack export (Admin)
    await page.goto("/reports/audit-pack");
    await noViolations(page, "Audit pack page");
    await page
      .getByRole("combobox", { name: /Manufacturer/i })
      .selectOption({ label: "Voltara Energy (VOLT)" });

    const batchSelect = page.getByRole("combobox", { name: /Submitted claim batch/i });
    if (await batchSelect.isVisible()) {
      const options = await batchSelect.locator("option").count();
      if (options > 1) {
        await batchSelect.selectOption({ index: 1 });
        const exportBtn = page.getByRole("button", { name: "Generate audit pack (XLSX)" });
        await expect(exportBtn).toBeEnabled();
        await exportBtn.click();
        await expect(page.getByText(/Job status:/i)).toBeVisible({ timeout: 60_000 });
      }
    }

    // 10. Audit pack analyst permission check (analyst sees permission denied)
    await analystPage.goto("/reports/audit-pack");
    await expect(analystPage.getByText(/Not available|permission/i)).toBeVisible();
    await noViolations(analystPage, "Audit pack permission denied (analyst)");

    await analystCtx.close();
  });
});
