import { expect } from "@playwright/test";

import { bootstrapRecovery, generated, test } from "./support";

test("S9 recovery case: assign, note, attachment, transition, reclaim, stale 412 banner", async ({
  page,
  browser,
}) => {
  test.setTimeout(420_000);
  const fixture = await bootstrapRecovery(page, browser);
  const target = fixture.cases[0];

  try {
    await page.goto(`/recovery/${target?.id ?? ""}`);
    const caseRegion = page.getByRole("region", { name: /Recovery case/ });
    await expect(caseRegion).toBeVisible();
    await expect(page.getByLabel("Case revision")).toBeVisible();

    // A second tab holds the old revision for the 412 check.
    const secondTab = await page.context().newPage();
    await secondTab.goto(`/recovery/${target?.id ?? ""}`);
    await expect(secondTab.getByLabel("Case revision")).toBeVisible();

    // Assign to me.
    await page.getByRole("button", { name: "Assign" }).click();
    const assignDialog = page.getByRole("dialog");
    await expect(assignDialog).toBeVisible();
    await assignDialog.getByLabel("Owner").selectOption({ label: "Assign to me" });
    await assignDialog.getByRole("button", { name: "Save assignment" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Owner updated." })).toBeVisible();
    await expect(page.getByLabel("Case owner")).toHaveText("You");

    // Add a note and see it in the timeline.
    const noteText = `Recovery note ${Date.now()}`;
    await page.getByLabel("Note", { exact: true }).fill(noteText);
    await page.getByRole("button", { name: "Add note" }).click();
    const timeline = page.getByRole("region", { name: "Timeline" });
    await expect(timeline).toContainText(noteText);
    await expect(timeline.getByRole("listitem").first()).toContainText(noteText);

    // Attach a file through the real upload flow.
    await page
      .getByLabel("Attachment file")
      .setInputFiles(generated("remittance/SYNTHETIC_credit_memo_VOLT_CM-7781.pdf"));
    await page.getByRole("button", { name: "Upload attachment" }).click();
    const attachments = page.getByRole("region", { name: "Attachments" });
    await expect(attachments.getByRole("button", { name: /Download attachment/ })).toHaveCount(1, {
      timeout: 60_000,
    });

    // Move to to_reclaim, then reclaim through the S7 queue.
    await page.getByRole("button", { name: "Change status" }).click();
    const statusDialog = page.getByRole("dialog");
    await expect(statusDialog).toBeVisible();
    await statusDialog.getByLabel("New status").selectOption("to_reclaim");
    await statusDialog.getByLabel("Note (optional)").fill("Reclaiming the short payment");
    await statusDialog.getByRole("button", { name: "Change status" }).click();
    await expect(page.getByLabel("Case status")).toHaveText("To reclaim");

    await page.getByRole("button", { name: "Reclaim" }).click();
    const reclaimDialog = page.getByRole("dialog");
    await expect(reclaimDialog).toBeVisible();
    await reclaimDialog.getByRole("button", { name: "Reclaim" }).click();
    await expect(page.getByText("Reclaim adjustment created.")).toBeVisible();
    const adjustmentLink = page.getByRole("link", { name: "View adjustment" });
    await expect(adjustmentLink).toBeVisible();
    await expect(adjustmentLink).toHaveAttribute("href", "/claims/adjustments?status=proposed");

    // The second tab still holds the pre-mutation revision: the server answers 412.
    await secondTab.getByRole("button", { name: "Change status" }).click();
    const staleDialog = secondTab.getByRole("dialog");
    await expect(staleDialog).toBeVisible();
    await staleDialog.getByLabel("New status").selectOption("recovered");
    await staleDialog.getByRole("button", { name: "Change status" }).click();
    await expect(secondTab.getByText("Someone else changed this case.")).toBeVisible();
    await secondTab.keyboard.press("Escape");
    await secondTab.getByRole("button", { name: "Reload" }).click();
    await expect(secondTab.getByText("Someone else changed this case.")).toHaveCount(0);
    await expect(secondTab.getByLabel("Case status")).toHaveText("To reclaim");

    await secondTab.close();
  } finally {
    for (const context of fixture.contexts) await context.close();
  }
});
