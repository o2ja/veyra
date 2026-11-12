import { expect } from "@playwright/test";

import { PASSWORD, clientIpHeader, enrolMfa, signIn, test, tokenFromMail, uniqueEmail } from "./support";

// W1 (register → verify → MFA setup) and W2 (invite analyst and viewer → accept → role limits).
// SYNTHETIC data only.

test("W1 + W2: register, verify, enrol MFA, invite an analyst and a viewer, role-limited access", async ({
  page,
  browser,
}) => {
  const admin = uniqueEmail("e2e-admin");

  // ---- W1 -----------------------------------------------------------------------------------
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Erin Admin");
  await page.getByLabel("Company name").fill("Acme Electric (SYNTHETIC)");
  await page.getByLabel("Work email").fill(admin);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();

  // before verification the account does not exist: same answer as a wrong password
  await signIn(page, admin);
  await expect(page.getByRole("alert")).toContainText("The email or password is incorrect.");

  await page.goto(`/verify-email#token=${await tokenFromMail(admin)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await expect(page).toHaveURL(/\/login\?reason=verified/);
  expect(page.url()).not.toContain("token=");

  await signIn(page, admin);
  await expect(page).toHaveURL(/\/mfa\/setup/); // the workspace requires MFA
  const { codes } = await enrolMfa(page);
  expect(codes).toHaveLength(10);
  await expect(page.getByRole("heading", { name: "Acme Electric (SYNTHETIC)" })).toBeVisible();

  const cookies = await page.context().cookies();
  const session = cookies.find((c) => c.name === "__Host-session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain("__Host-session");
  // no tokens or session data in web storage (CLAUDE.md §10)
  expect(await page.evaluate(() => Object.keys(window.localStorage).length)).toBe(0);

  // ---- W2 -----------------------------------------------------------------------------------
  const members = { analyst: uniqueEmail("e2e-analyst"), viewer: uniqueEmail("e2e-viewer") };
  await page.getByRole("link", { name: "Invitations" }).click();
  for (const [role, email] of Object.entries(members)) {
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Role").selectOption(role);
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByText(`Invitation created for ${email}`)).toBeVisible();
  }

  for (const [role, email] of Object.entries(members)) {
    const ctx = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
    const p = await ctx.newPage();
    await p.goto(`/accept-invite#token=${await tokenFromMail(email)}`);
    await p.getByLabel("Your name").fill(`Test ${role}`);
    await p.getByLabel("Password").fill(PASSWORD);
    await p.getByRole("button", { name: "Accept invitation" }).click();
    await expect(p).toHaveURL(/\/login\?reason=invited/);
    await signIn(p, email);
    await expect(p).toHaveURL(/\/mfa\/setup/);
    await enrolMfa(p);

    const nav = p.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Home" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Users" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Audit log" })).toHaveCount(role === "viewer" ? 1 : 0);
    await p.goto("/admin/users");
    await expect(p.getByRole("heading", { name: "Not available" })).toBeVisible();

    // the server refuses regardless of the UI (CSRF header sent the way the app sends it)
    const status = await p.evaluate(async () => {
      const csrf =
        document.cookie
          .split("; ")
          .find((c) => c.startsWith("__Host-csrf="))
          ?.split("=")[1] ?? "";
      const r = await fetch("/api/v1/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "If-Match": '"1"' },
        body: JSON.stringify({ mfa_required: false }),
      });
      return r.status;
    });
    expect(status).toBe(403);
    await ctx.close();
  }

  await page.getByRole("link", { name: "Users" }).click();
  for (const [role, email] of Object.entries(members)) {
    const row = page.getByRole("row").filter({ has: page.getByRole("cell", { name: email }) });
    await expect(row.getByRole("combobox")).toHaveValue(role);
  }
  await page.getByRole("link", { name: "Audit log" }).click();
  await expect(page.getByText("invitation.accepted").first()).toBeVisible();
  await expect(page.getByText("membership.created").first()).toBeVisible();
});
