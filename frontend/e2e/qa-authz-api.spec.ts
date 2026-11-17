import { expect } from "@playwright/test";

import {
  apiJson,
  apiStatus,
  clientIpHeader,
  inviteMember,
  registerWorkspace,
  test,
  uniqueEmail,
} from "./support";

// Direct backend calls: the UI hides actions from viewers/approvers, the API must refuse them itself.
test("API enforces roles, CSRF and validation, and writes persist", async ({ page, browser }) => {
  await registerWorkspace(page, uniqueEmail("qa-authz-admin"));
  const viewer = await inviteMember(page, browser, uniqueEmail("qa-authz-viewer"), "viewer", "QA Viewer");
  const approver = await inviteMember(
    page,
    browser,
    uniqueEmail("qa-authz-approver"),
    "approver",
    "QA Approver",
  );

  // signed-out client
  const anon = await browser.newContext({ extraHTTPHeaders: clientIpHeader() });
  const anonPage = await anon.newPage();
  expect(await apiStatus(anonPage, "GET", "/api/v1/manufacturers")).toBe(401);
  expect(
    await apiStatus(anonPage, "POST", "/api/v1/manufacturers", { name: "X", code: "X1" }),
  ).toBeGreaterThanOrEqual(401);
  await anon.close();

  // viewer: reads fine, every write is refused by the server
  expect(await apiStatus(viewer.page, "GET", "/api/v1/manufacturers")).toBe(200);
  const code = `QA${Date.now().toString(36).toUpperCase().slice(-6)}`;
  expect(await apiStatus(viewer.page, "POST", "/api/v1/manufacturers", { name: "Viewer Mfr", code })).toBe(
    403,
  );
  expect(
    await apiStatus(viewer.page, "POST", "/api/v1/invitations", { email: uniqueEmail("v"), role: "viewer" }),
  ).toBe(403);
  expect(await apiStatus(viewer.page, "GET", "/api/v1/settings")).toBe(200); // settings.read is open to every role by design
  expect(await apiStatus(viewer.page, "PATCH", "/api/v1/settings", {}, { "If-Match": '"1"' })).toBe(403);

  // approver: no user/settings administration
  expect(
    await apiStatus(approver.page, "POST", "/api/v1/invitations", {
      email: uniqueEmail("a"),
      role: "viewer",
    }),
  ).toBe(403);
  expect(await apiStatus(approver.page, "PATCH", "/api/v1/settings", {}, { "If-Match": '"1"' })).toBe(403);

  // CSRF: a signed-in write without the token is refused
  expect(
    await apiStatus(
      page,
      "POST",
      "/api/v1/manufacturers",
      { name: "No CSRF", code: `NC${code}` },
      { "X-CSRF-Token": "" },
    ),
  ).toBe(403);

  // admin: validation, create, update, duplicate, and persistence through a fresh read
  expect(await apiStatus(page, "POST", "/api/v1/manufacturers", { name: "", code })).toBe(422);
  const created = await apiJson<{ id: string; name: string }>(page, "POST", "/api/v1/manufacturers", {
    name: "QA Persist Mfr (SYNTHETIC)",
    code,
  });
  expect(await apiStatus(page, "POST", "/api/v1/manufacturers", { name: "Dup", code })).toBe(409);
  await apiJson(page, "PATCH", `/api/v1/manufacturers/${created.id}`, {
    name: "QA Persist Mfr Renamed (SYNTHETIC)",
  });
  const reread = await apiJson<{ name: string }>(viewer.page, "GET", `/api/v1/manufacturers/${created.id}`);
  expect(reread.name).toBe("QA Persist Mfr Renamed (SYNTHETIC)");

  await viewer.context.close();
  await approver.context.close();
});
