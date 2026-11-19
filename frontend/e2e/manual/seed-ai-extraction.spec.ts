import { readFileSync } from "node:fs";

import { expect, type Page } from "@playwright/test";

import { apiJson, generated, test, totp } from "../support";

// Manual, not CI: `E2E_MANUAL=1 SEED_AI=1 npx playwright test seed-ai-extraction`. Sends ONE fictional PDF to the real AI
// provider (Gemini) and enables AI for the workspace; AI is switched off again at the end.
test.skip(!process.env.SEED_AI, "manual seed, set SEED_AI=1");
test.setTimeout(300_000);

type Settings = { ai_processing_enabled: boolean; revision: number };
type Detail = { status: string; [key: string]: unknown };

const SOURCE = "agreements/SYNTHETIC_spa_voltara_SPA-VOLT-2026-884.pdf";
const EXPECTED = JSON.parse(
  readFileSync(
    generated("ground_truth/expected_extraction/SYNTHETIC_spa_voltara_SPA-VOLT-2026-884.json"),
    "utf8",
  ),
) as {
  header: {
    external_number: string;
    manufacturer_code: string;
    name: string;
    valid_from: string;
    valid_to: string;
  };
  items: { sku: string; sell_price: string; reference_cost: string; deviated_cost: string }[];
};

async function setAi(page: Page, enabled: boolean) {
  const current = await apiJson<Settings>(page, "GET", "/api/v1/settings");
  await apiJson(
    page,
    "PATCH",
    "/api/v1/settings",
    { ai_processing_enabled: enabled },
    {
      "If-Match": `"${current.revision}"`,
    },
  );
  const after = await apiJson<Settings>(page, "GET", "/api/v1/settings");
  expect(after.ai_processing_enabled).toBe(enabled);
}

test("one synthetic agreement is extracted by the AI pipeline and shows on AI usage", async ({ page }) => {
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
  page.on("pageerror", (e) => bad.push(`pageerror: ${e.message.slice(0, 120)}`));
  page.on(
    "response",
    (r) =>
      r.status() >= 400 &&
      !(r.status() === 409 && new URL(r.url()).pathname === "/api/v1/files") &&
      bad.push(`${r.status()} ${new URL(r.url()).pathname}`),
  );

  try {
    await setAi(page, true);

    await page.goto("/extractions");
    await expect(page.getByRole("heading", { name: "Document extractions" })).toBeVisible();
    await page.getByLabel("Or upload a new agreement file (PDF or XLSX)").setInputFiles(generated(SOURCE));
    // Submitting uploads the file; the API refuses Start until the malware scan is clean (no provider call then).
    await page.getByRole("button", { name: "Start extraction" }).click();
    const started = await page.waitForURL(/\/extractions\/[^/]+$/, { timeout: 8_000 }).then(
      () => true,
      () => false,
    );
    if (!started) {
      let fileId = "";
      await expect(async () => {
        const files = await apiJson<{
          data: { id: string; kind: string; scan_status: string; created_at: string }[];
        }>(page, "GET", "/api/v1/files?limit=50");
        const clean = files.data
          .filter((f) => f.kind === "agreement" && f.scan_status === "clean")
          .sort((a, b) => b.created_at.localeCompare(a.created_at));
        expect(clean.length).toBeGreaterThan(0);
        fileId = clean[0]?.id ?? "";
      }).toPass({ timeout: 120_000, intervals: [3_000] });
      await page.goto("/extractions");
      await page
        .getByRole("combobox")
        .filter({ has: page.locator("option", { hasText: "Choose an uploaded agreement file" }) })
        .selectOption(fileId);
      await page.getByRole("button", { name: "Start extraction" }).click();
      await page.waitForURL(/\/extractions\/[^/]+$/, { timeout: 60_000 });
    }
    const id = new URL(page.url()).pathname.split("/").pop() ?? "";

    let detail: Detail = { status: "unknown" };
    await expect(async () => {
      detail = await apiJson<Detail>(page, "GET", `/api/v1/document-extractions/${id}`);
      expect(["completed", "failed"]).toContain(detail.status);
    }).toPass({ timeout: 150_000, intervals: [3_000] });
    console.log(`extraction ${id} status: ${detail.status}`);
    expect(detail.status, JSON.stringify(detail).slice(0, 400)).toBe("completed");

    const blob = JSON.stringify(detail);
    const checks: [string, boolean][] = [
      [`external_number ${EXPECTED.header.external_number}`, blob.includes(EXPECTED.header.external_number)],
      [`manufacturer ${EXPECTED.header.manufacturer_code}`, blob.includes(EXPECTED.header.manufacturer_code)],
      [`name`, blob.includes(EXPECTED.header.name)],
      [`valid_from ${EXPECTED.header.valid_from}`, blob.includes(EXPECTED.header.valid_from)],
      [`valid_to ${EXPECTED.header.valid_to}`, blob.includes(EXPECTED.header.valid_to)],
      ...EXPECTED.items.flatMap((i): [string, boolean][] => [
        [`sku ${i.sku}`, blob.includes(i.sku)],
        [`sell_price ${i.sell_price}`, blob.includes(i.sell_price)],
        [`reference_cost ${i.reference_cost}`, blob.includes(i.reference_cost)],
        [`deviated_cost ${i.deviated_cost}`, blob.includes(i.deviated_cost)],
      ]),
    ];
    console.log(`field matches: ${checks.filter(([, ok]) => ok).length}/${checks.length}`);
    console.log(
      `field mismatches: ${
        checks
          .filter(([, ok]) => !ok)
          .map(([n]) => n)
          .join(", ") || "none"
      }`,
    );

    await page.goto("/extractions/usage");
    await expect(page.getByRole("heading", { name: "AI usage" })).toBeVisible();
    await expect(page.getByText("Enabled", { exact: true })).toBeVisible();
    const row = page.locator("table tbody tr").first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    console.log(`usage row: ${(await row.innerText()).replace(/\s+/g, " ")}`);
    const usage = await apiJson<{
      model: string;
      today: { tokens: number; cost_usd: string };
      daily: { calls: number; input_tokens: number; output_tokens: number; cost_usd: string }[];
    }>(page, "GET", "/api/v1/ai/usage");
    console.log(
      `usage model: ${usage.model}, today tokens: ${usage.today.tokens}, today cost: ${usage.today.cost_usd}`,
    );
    expect(usage.daily.length).toBeGreaterThan(0);
    expect(usage.daily.reduce((n, d) => n + d.calls, 0)).toBeGreaterThanOrEqual(1);
  } finally {
    await setAi(page, false);
  }
  expect(bad, bad.join(" | ")).toEqual([]);
});
