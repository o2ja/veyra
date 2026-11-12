import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, tokenFromMail, uniqueEmail } from "./support";

// W5 + W7: manual SPA & Rebate agreement drafts, validation, four-eyes approval, amendment, and diff.
// SYNTHETIC data only (from testdata/generated/agreements/manual_payloads/SPA-VOLT-2026-884.json & REB-VOLT-2026.json).

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

test("W5 + W7: SPA and Rebate drafts, validation error fix, four-eyes approval, amendment and diff", async ({
  page,
  browser,
}) => {
  const admin = uniqueEmail("e2e-agr-admin");
  const approver = uniqueEmail("e2e-agr-approver");

  // 1. Register admin + MFA
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Agreement Admin");
  await page.getByLabel("Company name").fill("Voltara Test Co (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await enrolMfa(page);

  // 2. Create manufacturer VOLT, customers ALDER/BLUFF/CEDAR, products BRK-3P-100A/BRK-3P-60A/PNL-200A
  await page.getByRole("link", { name: "Master data" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Voltara Electric (SYNTHETIC)");
  await page.getByLabel("Code", { exact: true }).fill("VOLT");
  await page.getByRole("button", { name: "Add manufacturer" }).click();
  await expect(page.getByText("Manufacturer added.")).toBeVisible();

  await page
    .getByRole("navigation", { name: "Master data" })
    .getByRole("link", { name: "Customers" })
    .click();
  await expect(page.getByRole("heading", { name: "Add a customer" })).toBeVisible();
  for (const [code, name] of [
    ["ALDER", "Alder Electric (SYNTHETIC)"],
    ["BLUFF", "Bluff Supply (SYNTHETIC)"],
    ["CEDAR", "Cedar Contractors (SYNTHETIC)"],
  ] as const) {
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByLabel("Code", { exact: true }).fill(code);
    await page.getByRole("button", { name: "Add customer" }).click();
    await expect(page.getByText("Customer added.")).toBeVisible();
  }

  await page.getByRole("navigation", { name: "Master data" }).getByRole("link", { name: "Products" }).click();
  await expect(page.getByRole("heading", { name: "Add a product" })).toBeVisible();
  for (const [sku, desc] of [
    ["BRK-3P-100A", "Breaker 3P 100A (SYNTHETIC)"],
    ["BRK-3P-60A", "Breaker 3P 60A (SYNTHETIC)"],
    ["PNL-200A", "Panel 200A (SYNTHETIC)"],
  ] as const) {
    await page.getByLabel("SKU", { exact: true }).fill(sku);
    await page.getByLabel("Description", { exact: true }).fill(desc);
    await page
      .getByLabel("Manufacturer", { exact: true })
      .selectOption({ label: "Voltara Electric (SYNTHETIC) (VOLT)" });
    await page.getByLabel("Base unit of measure").fill("EA");
    await page.getByRole("button", { name: "Add product" }).click();
    await expect(page.getByText("Product added.")).toBeVisible();
  }

  // 3. Invite an approver (W2 pattern)
  await page.getByRole("link", { name: "Invitations" }).click();
  await page.getByLabel("Email").fill(approver);
  await page.getByLabel("Role").selectOption("approver");
  await page.getByRole("button", { name: "Invite" }).click();
  await expect(page.getByText(`Invitation created for ${approver}`)).toBeVisible();

  const approverCtx = await browser.newContext();
  const approverPage = await approverCtx.newPage();
  await approverPage.goto(`/accept-invite#token=${await tokenFromMail(approver)}`);
  await approverPage.getByLabel("Your name").fill("Agreement Approver");
  await approverPage.getByLabel("Password").fill(PASSWORD);
  await approverPage.getByRole("button", { name: "Accept invitation" }).click();
  await expect(approverPage).toHaveURL(/\/login\?reason=invited/);
  await signIn(approverPage, approver);
  await expect(approverPage).toHaveURL(/\/mfa\/setup/);
  await enrolMfa(approverPage);

  // 4. Create SPA-VOLT-2026-884 draft, trigger & fix one validation error, submit
  await page.getByRole("link", { name: "Agreements" }).click();
  await expect(page.getByRole("heading", { name: "Agreements" })).toBeVisible();
  await noViolations(page, "/agreements");
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts$/);
  await noViolations(page, "/agreement-drafts");

  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const spaDraftUrl = page.url();
  await noViolations(page, "/agreement-drafts/:id");

  await page.getByLabel("Agreement type").selectOption("spa");
  await page.getByLabel("External number").fill("SPA-VOLT-2026-884");
  await page.getByLabel("Manufacturer code").fill("VOLT");
  await page.getByLabel("Name", { exact: true }).fill("Voltara contractor SPA 2026 (SYNTHETIC)");
  await page.getByLabel("Priority").fill("0");
  // Intentionally invert valid_from / valid_to to observe a validation error first
  await page.getByLabel("Valid from").fill("2026-12-31");
  await page.getByLabel("Valid to").fill("2026-01-01");

  await page.getByLabel("Date basis").selectOption("ship_date");
  await page.getByLabel("Calculation basis").selectOption("price_difference");
  await page.getByLabel("Cost source").selectOption("agreement_reference");
  await page.getByLabel("Customer scope").selectOption("listed");
  await page.getByLabel("Price check mode").selectOption("flag");
  await page.getByLabel("Price check tolerance").fill("0.005");
  await page.getByLabel("Quantity cap scope").selectOption("per_item");

  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "Add customer" }).click();
  }
  for (const [i, code] of ["ALDER", "BLUFF", "CEDAR"].entries()) {
    await page.getByLabel("Customer code").nth(i).fill(code);
  }

  const spaItems = [
    { sku: "BRK-3P-100A", ref: "100.00", dev: "78.00", sell: "88.00", maxQty: "" },
    { sku: "BRK-3P-60A", ref: "64.50", dev: "52.00", sell: "58.00", maxQty: "" },
    { sku: "PNL-200A", ref: "412.00", dev: "360.00", sell: "389.00", maxQty: "100" },
  ];
  for (const [i, it] of spaItems.entries()) {
    await page.getByRole("button", { name: "Add item" }).click();
    await page.getByLabel(`Item ${i + 1} SKU`).fill(it.sku);
    await page.getByLabel(`Item ${i + 1} reference cost`).fill(it.ref);
    await page.getByLabel(`Item ${i + 1} deviated cost`).fill(it.dev);
    await page.getByLabel(`Item ${i + 1} sell price`).fill(it.sell);
    if (it.maxQty) {
      await page.getByLabel(`Item ${i + 1} max quantity`).fill(it.maxQty);
    }
  }

  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText(/validation error\(s\)/)).toBeVisible();

  // Fix the validation error
  await page.getByLabel("Valid from").fill("2026-01-01");
  await page.getByLabel("Valid to").fill("2026-12-31");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validation passed.")).toBeVisible();

  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

  // Author's Approve button is disabled (four-eyes rule)
  await expect(page.getByRole("button", { name: "Approve draft" })).toBeDisabled();

  // 5. Approver approves SPA draft and checks version detail
  const spaDraftId = spaDraftUrl.split("/").pop() ?? "";
  await approverPage.goto(`/agreement-drafts/${spaDraftId}`);
  await expect(approverPage.getByRole("button", { name: "Approve draft" })).toBeEnabled();
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();
  await approverPage.getByRole("link", { name: "View approved version" }).click();
  await expect(approverPage).toHaveURL(/\/agreements\/versions\/[0-9a-f-]+$/);
  await expect(approverPage.getByRole("heading", { name: "Items (3)" })).toBeVisible();
  await expect(approverPage.getByRole("cell", { name: "BRK-3P-100A" })).toBeVisible();
  await expect(approverPage.getByRole("cell", { name: "PNL-200A" })).toBeVisible();
  await noViolations(approverPage, "/agreements/versions/:id");

  // 6. Create & approve Rebate draft REB-VOLT-2026
  await page.getByRole("navigation", { name: "Agreements" }).getByRole("link", { name: "Drafts" }).click();
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const rebDraftId = page.url().split("/").pop() ?? "";

  await page.getByLabel("Agreement type").selectOption("rebate");
  await page.getByLabel("External number").fill("REB-VOLT-2026");
  await page.getByLabel("Manufacturer code").fill("VOLT");
  await page.getByLabel("Name", { exact: true }).fill("Voltara annual volume rebate 2026 (SYNTHETIC)");
  await page.getByLabel("Valid from").fill("2026-01-01");
  await page.getByLabel("Valid to").fill("2026-12-31");
  await page.getByLabel("Volume basis").selectOption("purchases");
  await page.getByLabel("Tier metric").selectOption("amount");
  await page.getByLabel("Tier mode").selectOption("retroactive");
  await page.getByLabel("SPA lines").selectOption("include");
  await page.getByLabel("Product scope").selectOption("all");
  await page.getByLabel("Measurement period kind").fill("calendar_year");
  await page.getByLabel("Measurement period start").fill("2026-01-01");
  await page.getByLabel("Measurement period end").fill("2026-12-31");

  const tiers = [
    { bound: "0", rate: "0.015" },
    { bound: "1000000", rate: "0.025" },
    { bound: "2000000", rate: "0.035" },
  ];
  for (const [i, tier] of tiers.entries()) {
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

  await approverPage.goto(`/agreement-drafts/${rebDraftId}`);
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();

  // 7. Amend SPA-VOLT-2026-884 -> version history shows superseded + active -> diff shows changed field
  await page
    .getByRole("navigation", { name: "Agreements" })
    .getByRole("link", { name: "Agreements" })
    .click();
  await page.getByRole("link", { name: "SPA-VOLT-2026-884" }).click();
  await expect(page.getByRole("heading", { name: "Version history" })).toBeVisible();
  await noViolations(page, "/agreements/:id");
  const spaAgreementUrl = page.url();

  await page.getByRole("button", { name: "Amend version" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const amendDraftId = page.url().split("/").pop() ?? "";
  await page.getByLabel("Priority", { exact: true }).fill("7"); // versioned; the name is per agreement
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validation passed.")).toBeVisible();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

  await approverPage.goto(`/agreement-drafts/${amendDraftId}`);
  await approverPage.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverPage.getByText("Draft approved.")).toBeVisible();
  await approverCtx.close();

  await page.goto(spaAgreementUrl);
  const history = page.getByRole("table", { name: "Version history" });
  await expect(history.getByText("v2")).toBeVisible();
  await expect(history.getByText("Active")).toBeVisible();
  await expect(history.getByText("v1")).toBeVisible();
  await expect(history.getByText("Superseded")).toBeVisible();

  await history.getByRole("link", { name: "Diff prior" }).first().click();
  await expect(page).toHaveURL(/\/agreements\/versions\/[0-9a-f-]+\/diff\/[0-9a-f-]+$/);
  const diffTable = page.getByRole("table", { name: "Version differences" });
  await expect(diffTable).toContainText("priority");
  await expect(diffTable).toContainText("7");
  await noViolations(page, "/agreements/versions/:id/diff/:otherId");
});
