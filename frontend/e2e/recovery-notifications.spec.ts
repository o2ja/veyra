import { expect } from "@playwright/test";

import { bootstrapRecovery, expectNoA11yViolations, test } from "./support";

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

    const items = card.getByRole("listitem");
    const unreadCount = await items.count();
    if (unreadCount > 0) {
      const heading = items.first().getByRole("heading", { level: 2 });
      const firstTitle = await heading.innerText();
      const sameTitle = card.getByRole("heading", { name: firstTitle, exact: true });
      const before = await sameTitle.count();
      await items
        .first()
        .getByRole("button", { name: /Mark read/ })
        .click();
      await expect(sameTitle).toHaveCount(before - 1);
    } else {
      // Reminders only exist after the server's daily sweep; no reminder means the empty state.
      await expect(card.getByText("No unread notifications.")).toBeVisible();
    }

    await page.getByLabel("Unread only").uncheck();
    const allCount = await card.getByRole("listitem").count();
    if (allCount === 0) {
      await expect(card.getByText("No notifications yet.")).toBeVisible();
    } else {
      await expect(card.getByRole("listitem")).toHaveCount(allCount);
    }
    await expectNoA11yViolations(page, "/notifications (all)");
  } finally {
    for (const context of fixture.contexts) await context.close();
  }
});
