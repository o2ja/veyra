import { expect } from "@playwright/test";

import { apiJson, apiStatus, bootstrapRecovery, inviteMember, test, uniqueEmail } from "./support";

test("S9 write-off: propose, same user disabled, second approver completes, analyst forbidden", async ({
  page,
  browser,
}) => {
  test.setTimeout(420_000);
  const fixture = await bootstrapRecovery(page, browser, { twoApprovers: true });
  const approverB = fixture.approverB;
  if (!approverB) throw new Error("second approver missing from the fixture");
  const target = fixture.cases.find((item) => item.case_type === "short_paid");
  if (!target) throw new Error("short-paid recovery case missing from the fixture");
  const caseId = target.id;

  const analyst = await inviteMember(
    page,
    browser,
    uniqueEmail("e2e-recovery-analyst"),
    "analyst",
    "Recovery Analyst",
  );

  try {
    // Approver A proposes (the first call records the proposal).
    await fixture.approverA.goto(`/recovery/${caseId}`);
    await expect(fixture.approverA.getByLabel("Case status")).toBeVisible();
    await fixture.approverA.getByRole("button", { name: "Write off" }).click();
    const proposeDialog = fixture.approverA.getByRole("dialog");
    await expect(proposeDialog).toBeVisible();
    await proposeDialog.getByLabel("Reason").fill("Manufacturer will not accept the claim");
    await proposeDialog.getByRole("button", { name: "Write off" }).click();
    await expect(
      fixture.approverA.getByText("Write-off proposed; a second approver must complete it."),
    ).toBeVisible();
    const completeButton = fixture.approverA.getByRole("button", { name: "Complete write-off" });
    await expect(completeButton).toBeVisible();
    await expect(completeButton).toBeDisabled();

    // A different approver completes the four-eyes flow.
    await approverB.goto(`/recovery/${caseId}`);
    await expect(approverB.getByRole("button", { name: "Approve write-off" })).toBeVisible();
    await approverB.getByRole("button", { name: "Approve write-off" }).click();
    const completeDialog = approverB.getByRole("dialog");
    await expect(completeDialog).toBeVisible();
    await completeDialog.getByRole("button", { name: "Approve write-off" }).click();
    await expect(approverB.getByText("Case written off.")).toBeVisible();
    await expect(approverB.getByLabel("Case status")).toHaveText("Written off");

    const written = await apiJson<{ written_off_amount: string; status: string }>(
      approverB,
      "GET",
      `/api/v1/cases/${caseId}`,
    );
    expect(written.status).toBe("written_off");
    await expect(approverB.getByLabel("Written off amount")).toHaveText(written.written_off_amount);

    // An analyst gets no write-off action in the UI and a 403 from the API.
    await analyst.page.goto(`/recovery/${caseId}`);
    await expect(analyst.page.getByRole("region", { name: /Recovery case/ })).toBeVisible();
    await expect(analyst.page.getByRole("button", { name: "Write off" })).toHaveCount(0);
    await expect(analyst.page.getByRole("button", { name: "Approve write-off" })).toHaveCount(0);
    await expect(analyst.page.getByRole("button", { name: "Complete write-off" })).toHaveCount(0);
    const current = await apiJson<{ revision: number }>(analyst.page, "GET", `/api/v1/cases/${caseId}`);
    const status = await apiStatus(
      analyst.page,
      "POST",
      `/api/v1/cases/${caseId}/write-off`,
      { reason: "analyst must not write off" },
      { "If-Match": `"${current.revision}"`, "Idempotency-Key": crypto.randomUUID() },
    );
    expect(status).toBe(403);
  } finally {
    await analyst.context.close();
    for (const context of fixture.contexts) await context.close();
  }
});
