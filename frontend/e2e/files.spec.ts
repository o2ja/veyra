import { expect, test } from "@playwright/test";

import { PASSWORD, enrolMfa, signIn, tokenFromMail, uniqueEmail } from "./support";

// S2: upload -> quarantine -> ClamAV scan by the worker -> clean (downloadable) or blocked.
// Needs the full stack: API, worker, SeaweedFS, ClamAV, Mailpit. SYNTHETIC data only.

function eicar(): Buffer {
  // assembled at runtime so no file in the repository carries the test signature
  return Buffer.from(
    "WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJ" + "Q0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo=",
    "base64",
  );
}

test("upload a clean file and an infected file; only the clean one is downloadable", async ({ page }) => {
  const admin = uniqueEmail("e2e-files");
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Files Admin");
  await page.getByLabel("Company name").fill("Files Co (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, admin);
  await enrolMfa(page);

  await page.getByRole("link", { name: "Files" }).click();
  const clean = `sales-${Date.now()}.csv`;
  await page.getByLabel("Type").selectOption("import");
  await page.getByLabel("File", { exact: true }).setInputFiles({
    name: clean,
    mimeType: "text/csv",
    buffer: Buffer.from(`invoice,amount\nE2E-${Date.now()},10.00\n`),
  });
  await page.getByRole("button", { name: "Upload" }).click();
  // first write after a storage restart allocates volumes (cold start can take ~25 s)
  await expect(page.getByText(`${clean} uploaded`)).toBeVisible({ timeout: 60_000 });

  await page.getByLabel("Type").selectOption("attachment");
  await page
    .getByLabel("File", { exact: true })
    .setInputFiles({ name: "remittance.csv", mimeType: "text/csv", buffer: eicar() });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("remittance.csv uploaded")).toBeVisible();

  const cleanRow = page.getByRole("row").filter({ hasText: clean });
  const badRow = page.getByRole("row").filter({ hasText: "remittance.csv" });
  await expect(cleanRow.getByRole("status")).toHaveText("Clean", { timeout: 60_000 });
  await expect(badRow.getByRole("status")).toHaveText("Blocked: malware found", { timeout: 60_000 });
  await expect(badRow.getByRole("button", { name: "Download" })).toHaveCount(0);

  const download = page.waitForEvent("download");
  await cleanRow.getByRole("button", { name: "Download" }).click();
  expect((await download).suggestedFilename()).toBe(clean);

  await cleanRow.getByRole("link", { name: clean }).click();
  await expect(page.getByRole("heading", { name: clean })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Clean");
  await expect(page.getByText("SHA-256")).toBeVisible();
});
