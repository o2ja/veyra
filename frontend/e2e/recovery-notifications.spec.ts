import { expect } from "@playwright/test";

import { apiJson, bootstrapRecovery, expectNoA11yViolations, test } from "./support";

test("S9 notifications: unread/all list, mark read, axe on queue, case and notifications", async ({
  page,
  browser,
}) => {
  test.setTimeout(360_000);
  const fixture = await bootstrapRecovery(page, browser);
  const target = fixture.cases[0];

  try {
    // Queue.
    await page.goto("/recovery");
    await expect(page.getByRole("table", { name: "Recovery cases" })).toBeVisible();
    await expectNoA11yViolations(page, "/recovery");

    // Case.
    await page.goto(`/recovery/${target?.id ?? ""}`);
    await expect(page.getByRole("region", { name: /Recovery case/ })).toBeVisible();
    await expectNoA11yViolations(page, `/recovery/${target?.id ?? ""}`);

    // Notifications.
    await page.goto("/notifications");
    const card = page.getByRole("region", { name: "Notifications" });
    await expect(card).toBeVisible();
    await expectNoA11yViolations(page, "/notifications");

    // The server decides whether reminders exist (daily sweep); the UI must show exactly that.
    const unread = await apiJson<{ data: { title: string }[] }>(
      page,
      "GET",
      "/api/v1/notifications?unread=true&limit=50",
    );
    const items = card.getByRole("listitem");
    await expect(items).toHaveCount(unread.data.length);
    if (unread.data.length === 0) {
      await expect(card.getByText("No unread notifications.")).toBeVisible();
    } else {
      await items
        .first()
        .getByRole("button", { name: /Mark read/ })
        .click();
      await expect(items).toHaveCount(unread.data.length - 1);
    }

    await page.getByLabel("Unread only").uncheck();
    const all = await apiJson<{ data: unknown[] }>(
      page,
      "GET",
      "/api/v1/notifications?unread=false&limit=50",
    );
    await expect(card.getByRole("listitem")).toHaveCount(all.data.length);
    if (all.data.length === 0) await expect(card.getByText("No notifications yet.")).toBeVisible();
    await expectNoA11yViolations(page, "/notifications (all)");
  } finally {
    for (const context of fixture.contexts) await context.close();
  }
});
