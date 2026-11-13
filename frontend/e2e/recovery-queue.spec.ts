import { expect } from "@playwright/test";

import {
  apiJson,
  bootstrapRecovery,
  expectNoA11yViolations,
  inviteMember,
  test,
  uniqueEmail,
} from "./support";

test("S9 recovery queue: server order, unknown deadline first, reason filter, summary, viewer read-only", async ({
  page,
  browser,
}) => {
  test.setTimeout(360_000);
  const fixture = await bootstrapRecovery(page, browser);
  const viewer = await inviteMember(
    page,
    browser,
    uniqueEmail("e2e-recovery-viewer"),
    "viewer",
    "Recovery Viewer",
  );

  try {
    expect(fixture.cases).toHaveLength(4);
    const unknownFirst = fixture.cases[0];
    expect(unknownFirst?.deadline_unknown).toBe(true);

    await page.getByRole("link", { name: "Recovery" }).click();
    await expect(page).toHaveURL("/recovery");

    const summary = page.getByRole("region", { name: "Recovery summary" });
    const serverSummary = await apiJson<{
      at_risk: { deadline_within_days: number; count: number; amount: string };
    }>(page, "GET", "/api/v1/cases/summary");
    await expect(summary).toContainText(
      `At risk within ${serverSummary.at_risk.deadline_within_days} days: ${serverSummary.at_risk.count} cases, ${serverSummary.at_risk.amount}`,
    );

    const table = page.getByRole("table", { name: "Recovery cases" });
    const rows = table.locator("tbody tr");
    await expect(rows).toHaveCount(4);
    const firstRow = rows.first();
    await expect(firstRow.getByText("Unknown deadline")).toBeVisible();
    await expect(firstRow).toContainText(unknownFirst?.variance_amount ?? "");

    // The reason filter narrows the queue exactly as the server does.
    const reason = unknownFirst?.reason_code ?? "";
    expect(reason).not.toBe("");
    const filtered = await apiJson<{ data: { id: string }[] }>(
      page,
      "GET",
      `/api/v1/cases?reason_code=${encodeURIComponent(reason)}&limit=50`,
    );
    await page.getByLabel("Reason").selectOption(reason);
    await expect(page).toHaveURL(new RegExp(`reason_code=${reason}`));
    await expect(rows).toHaveCount(filtered.data.length);

    await expectNoA11yViolations(page, "/recovery");

    // A viewer reads the case but gets no action buttons.
    await viewer.page.goto(`/recovery/${unknownFirst?.id ?? ""}`);
    await expect(viewer.page.getByRole("region", { name: /Recovery case/ })).toBeVisible();
    await expect(viewer.page.getByRole("button", { name: "Assign" })).toHaveCount(0);
    await expect(viewer.page.getByRole("button", { name: "Change status" })).toHaveCount(0);
    await expect(viewer.page.getByRole("button", { name: "Reclaim" })).toHaveCount(0);
    await expect(viewer.page.getByRole("button", { name: "Write off" })).toHaveCount(0);
  } finally {
    await viewer.context.close();
    for (const context of fixture.contexts) await context.close();
  }
});
