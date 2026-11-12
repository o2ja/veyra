import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, test, tokenFromMail, uniqueEmail } from "./support";

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

test("S6 extractions: AI disabled shows ai_not_enabled message and AI usage page renders", async ({
  page,
}) => {
  const admin = uniqueEmail("e2e-ext-admin");

  await page.goto("/register");
  await page.getByLabel("Your name").fill("Extraction Admin");
  await page.getByLabel("Company name").fill("Voltara AI Co (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await enrolMfa(page);

  // Navigate to Extractions
  await page.getByRole("link", { name: "Extractions" }).click();
  await expect(page.getByRole("heading", { name: "Document extractions" })).toBeVisible();
  await noViolations(page, "/extractions");

  // Upload a minimal synthetic XLSX/PDF buffer and attempt to start extraction while AI is disabled
  await page.getByLabel("Or upload a new agreement file (PDF or XLSX)").setInputFiles({
    name: "synthetic-agreement.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"),
  });
  await page.getByRole("button", { name: "Start extraction" }).click();
  await expect(page.getByText(/AI extraction is not enabled/i)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: "Enable it in workspace settings" })).toBeVisible();

  // Navigate to AI usage
  await page.getByRole("navigation", { name: "Extractions" }).getByRole("link", { name: "AI usage" }).click();
  await expect(page.getByRole("heading", { name: "AI usage" })).toBeVisible();
  await expect(page.getByText("Disabled")).toBeVisible();
  await noViolations(page, "/extractions/usage");
});
