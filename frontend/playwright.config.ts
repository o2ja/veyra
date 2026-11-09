import { defineConfig } from "@playwright/test";

// End-to-end tests against the real stack (API, Postgres, Mailpit, Vite dev server). The browser
// origin must be PUBLIC_BASE_URL (http://localhost:5173): Origin check and __Host- cookies depend on it.
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["junit", { outputFile: "junit-e2e.xml" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    // locally the installed Chrome; CI installs Playwright's Chromium
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
    trace: "retain-on-failure",
  },
});
