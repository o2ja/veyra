import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, tokenFromMail, uniqueEmail } from "./support";

// S3 master data screens against the real API: create, edit, persist. SYNTHETIC data only.

async function noViolations(page: Page, name: string) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = result.violations.map((v) => `${v.id} (${v.nodes.length})`);
  expect(summary, `${name}: ${summary.join(", ")}`).toEqual([]);
}

test("master data: manufacturer and settings, customer and alias, product, UoM conversion", async ({
  page,
}) => {
  const admin = uniqueEmail("e2e-master");
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Master Admin");
  await page.getByLabel("Company name").fill("Master Co (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await enrolMfa(page);

  // manufacturer -> detail -> claim settings saved and still there after a reload
  await page.getByRole("link", { name: "Master data" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Voltara Electric (SYNTHETIC)");
  await page.getByLabel("Code", { exact: true }).fill("VOLT");
  await page.getByRole("button", { name: "Add manufacturer" }).click();
  await page.getByRole("link", { name: "Voltara Electric (SYNTHETIC)" }).click();
  await expect(page.getByRole("heading", { name: "Claim and dispute settings" })).toBeVisible();
  await noViolations(page, "/master-data/manufacturers/:id");
  await page.getByLabel("Claim window (days)").fill("60");
  await page.getByLabel("Claim window starts at").selectOption("invoice_date"); // days and basis go together
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Claim window (days)")).toHaveValue("60");

  // customer -> alias
  await page
    .getByRole("navigation", { name: "Master data" })
    .getByRole("link", { name: "Customers" })
    .click();
  await page.getByLabel("Name", { exact: true }).fill("Alder Electric LLC (SYNTHETIC)");
  await page.getByLabel("Code", { exact: true }).fill("ALDER");
  await page.getByRole("button", { name: "Add customer" }).click();
  await expect(page.getByText("Customer added.")).toBeVisible();
  await page.getByLabel("Search", { exact: true }).fill("ALDER");
  await page
    .getByRole("table", { name: "Customers" })
    .getByRole("link", { name: /Alder Electric/ })
    .click();
  await page.getByLabel("New alias").fill("ALDER ELEC");
  await page.getByRole("button", { name: "Add alias" }).click();
  await expect(page.getByRole("table", { name: "Customer aliases" }).getByText("ALDER ELEC")).toBeVisible();
  await noViolations(page, "/master-data/customers/:id");

  // product with a leading-zero SKU (kept as text)
  await page.getByRole("navigation", { name: "Master data" }).getByRole("link", { name: "Products" }).click();
  await page.getByLabel("SKU", { exact: true }).fill("00418");
  await page.getByLabel("Description", { exact: true }).fill("Connector 00418 (SYNTHETIC)");
  await page
    .getByLabel("Manufacturer", { exact: true })
    .selectOption({ label: "Voltara Electric (SYNTHETIC) (VOLT)" });
  await page.getByLabel("Base unit of measure").fill("EA");
  await page.getByRole("button", { name: "Add product" }).click();
  await expect(page.getByText("Product added.")).toBeVisible();
  await page.getByRole("table", { name: "Products" }).getByRole("link", { name: "00418" }).click();
  await expect(page.getByLabel("SKU", { exact: true })).toHaveValue("00418");
  await noViolations(page, "/master-data/products/:id");

  // a global UoM conversion
  await page
    .getByRole("navigation", { name: "Master data" })
    .getByRole("link", { name: "UoM conversions" })
    .click();
  await page.getByLabel("From unit").fill("RL");
  await page.getByLabel("To unit").fill("FT");
  await page.getByLabel("Factor", { exact: true }).fill("500");
  const created = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/uom-conversions") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Add conversion" }).click();
  const res = await created;
  expect(res.status(), await res.text()).toBe(201);
  await expect(page.getByText("Conversion added.")).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Unit-of-measure conversions" }).getByText("500"),
  ).toBeVisible();
});
