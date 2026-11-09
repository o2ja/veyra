import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { RequirePermission, RequireSession } from "../app/guards";
import { LoginPage } from "../features/auth/login-pages";
import { api } from "./api/client";
import { takeFragmentToken } from "./fragment-token";
import { SessionProvider } from "./session";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const ME = {
  user: { id: "u1", email: "a@example.test", mfa_enabled: true },
  tenant: { id: "t1", name: "Acme" },
  role: "viewer",
  permissions: ["settings.read", "audit.read"],
  mfa_pending: false,
  enrollment_required: false,
};

beforeEach(() => {
  document.cookie = "__Host-csrf=; Secure; Path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("mutating requests carry the CSRF cookie value; reads do not", async () => {
  document.cookie = "__Host-csrf=abc123; Secure; Path=/";
  const seen: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((r: Request) => {
      seen.push(r);
      return Promise.resolve(json({ status: "ok" }));
    }),
  );
  await api.GET("/api/v1/health/live");
  await api.POST("/api/v1/auth/logout");
  expect(seen[0]?.headers.get("X-CSRF-Token")).toBeNull();
  expect(seen[1]?.headers.get("X-CSRF-Token")).toBe("abc123");
});

test("a pre-session CSRF token is fetched before the first POST", async () => {
  const seen: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((r: Request) => {
      seen.push(r);
      if (r.url.endsWith("/auth/csrf")) document.cookie = "__Host-csrf=fresh; Secure; Path=/";
      return Promise.resolve(json({ status: "accepted", message: "x" }, 202));
    }),
  );
  await api.POST("/api/v1/auth/password/forgot", { body: { email: "a@example.test" } });
  expect(seen.map((r) => new URL(r.url).pathname)).toEqual([
    "/api/v1/auth/csrf",
    "/api/v1/auth/password/forgot",
  ]);
  expect(seen[1]?.headers.get("X-CSRF-Token")).toBe("fresh");
});

test("the fragment token is read once and removed from the address bar", () => {
  window.history.replaceState(null, "", "/reset-password#token=abcdefghijklmnopqrstuvwxyz012345");
  expect(takeFragmentToken()).toBe("abcdefghijklmnopqrstuvwxyz012345");
  expect(window.location.hash).toBe("");
  expect(takeFragmentToken()).toBe("abcdefghijklmnopqrstuvwxyz012345"); // StrictMode re-run
  window.history.replaceState(null, "", "/reset-password#token=<script>");
  expect(takeFragmentToken()).toBeNull();
});

function renderAt(path: string, me: unknown, ui: React.ReactNode) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        me ? json(me) : json({ error: { code: "unauthenticated", message: "Sign in.", details: [] } }, 401),
      ),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SessionProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/login" element={<p>login page</p>} />
            <Route path="/login/mfa" element={<p>mfa page</p>} />
            <Route path="/mfa/setup" element={<p>setup page</p>} />
            <Route path="*" element={ui} />
          </Routes>
        </MemoryRouter>
      </SessionProvider>
    </QueryClientProvider>,
  );
}

test("signed-out users are sent to sign in", async () => {
  renderAt("/admin/users", null, <RequireSession>secret</RequireSession>);
  expect(await screen.findByText("login page")).toBeTruthy();
});

test("pending MFA and forced enrolment are routed by the server's session state", async () => {
  renderAt("/x", { ...ME, mfa_pending: true }, <RequireSession>secret</RequireSession>);
  expect(await screen.findByText("mfa page")).toBeTruthy();
  cleanup();
  renderAt("/x", { ...ME, enrollment_required: true }, <RequireSession>secret</RequireSession>);
  expect(await screen.findByText("setup page")).toBeTruthy();
});

test("pages outside the role show the permission-denied state", async () => {
  renderAt(
    "/admin/users",
    ME,
    <RequireSession>
      <RequirePermission permission="user.manage">users list</RequirePermission>
    </RequireSession>,
  );
  expect(await screen.findByText("Not available")).toBeTruthy();
  expect(screen.queryByText("users list")).toBeNull();
});

test("login shows the server's generic error message", async () => {
  document.cookie = "__Host-csrf=t; Secure; Path=/";
  vi.stubGlobal(
    "fetch",
    vi.fn((r: Request) =>
      Promise.resolve(
        r.url.endsWith("/auth/me")
          ? json({ error: { code: "unauthenticated", message: "Sign in.", details: [] } }, 401)
          : json(
              {
                error: {
                  code: "unauthenticated",
                  message: "The email or password is incorrect.",
                  details: [],
                },
              },
              401,
            ),
      ),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SessionProvider>
        <MemoryRouter initialEntries={["/login"]}>
          <LoginPage />
        </MemoryRouter>
      </SessionProvider>
    </QueryClientProvider>,
  );
  await userEvent.type(screen.getByLabelText("Email"), "a@example.test");
  await userEvent.type(screen.getByLabelText("Password"), "wrong password");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect((await screen.findByRole("alert")).textContent).toContain("The email or password is incorrect.");
});
