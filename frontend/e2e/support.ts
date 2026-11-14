import { createHmac, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Browser, type BrowserContext, type Page } from "@playwright/test";

/** A random private client address, forwarded by the dev proxy (which CI trusts), so per-IP limits such as
 * register (5/h) do not leak across tests. Use it for extra browser contexts too. */
export function clientIpHeader(): Record<string, string> {
  const n = randomBytes(2);
  return { "X-Forwarded-For": `10.${n[0] ?? 0}.${n[1] ?? 0}.1` };
}

// A fixture, not a beforeEach in this module: a helper module is evaluated once per worker, so a hook declared
// here bound only to the first spec file that imported it and every later spec shared one client IP.
export const test = base.extend({
  context: async ({ context }, provide) => {
    await context.setExtraHTTPHeaders(clientIpHeader());
    await provide(context);
  },
});

export const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://localhost:8025";
export const PASSWORD = "correct horse battery staple 42";

export function uniqueEmail(prefix: string): string {
  return `${prefix}-${randomBytes(5).toString("hex")}@example.test`;
}

interface MailSummary {
  ID: string;
}

/** Wait for the newest email to `to` and return the token from its link fragment. */
export async function tokenFromMail(to: string, expect = 1): Promise<string> {
  for (let i = 0; i < 100; i++) {
    const res = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`);
    const found = (await res.json()) as { messages: MailSummary[] };
    if (found.messages.length >= expect) {
      const newest = found.messages[0];
      if (!newest) continue;
      const msg = (await (await fetch(`${MAILPIT}/api/v1/message/${newest.ID}`)).json()) as {
        Text: string;
      };
      const match = /#token=([A-Za-z0-9_-]+)/.exec(msg.Text);
      if (!match?.[1]) throw new Error("no link in email");
      return match[1];
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`no email for ${to}`);
}

function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "").toUpperCase())
    bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = bits.match(/.{8}/g) ?? [];
  return Buffer.from(bytes.map((b) => parseInt(b, 2)));
}

/** RFC 6238 TOTP (SHA-1, 6 digits, 30 s), as authenticator apps compute it. */
export function totp(secret: string, at = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const h = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = (h.at(-1) ?? 0) & 0xf;
  const code = (h.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

/** On the MFA setup page: reveal the key, enter a code, return the secret and the recovery codes. */
export async function enrolMfa(page: Page): Promise<{ secret: string; codes: string[] }> {
  await page.getByRole("button", { name: "Start setup" }).click();
  await expect(page.getByRole("img", { name: "QR code for your authenticator app" })).toBeVisible();
  await page.getByRole("button", { name: "Can't scan? Show the setup key" }).click();
  const secret = (await page.locator("code").innerText()).trim();
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Turn on two-factor authentication" }).click();
  const list = page.getByRole("list", { name: "Recovery codes" });
  await expect(list).toBeVisible();
  const codes = await list.locator("li").allInnerTexts();
  await page.getByRole("button", { name: "I saved them, continue" }).click();
  // the button refreshes the session, then navigates to "/": a click before that lands is undone by it (e2e flake)
  await page.waitForURL((url) => url.pathname === "/");
  return { secret, codes };
}

export async function signIn(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

// ---------------------------------------------------------------------------
// S9 recovery helpers: the real stack only (no mocks). Registration and the
// data import use the same UI path as the earlier feature specs; the S8
// remittance finalisation runs through the API because S8's screens are not
// part of this branch.
// ---------------------------------------------------------------------------

export const generated = (rel: string): string =>
  fileURLToPath(new URL(`../../testdata/generated/${rel}`, import.meta.url));

export async function registerWorkspace(
  page: Page,
  email: string,
  name = "Recovery Admin",
  workspace = "Voltara Recovery Co (SYNTHETIC)",
): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Your name").fill(name);
  await page.getByLabel("Company name").fill(workspace);
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.goto(`/verify-email#token=${await tokenFromMail(email)}`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Confirm and create workspace" }).click();
  await signIn(page, email);
  await enrolMfa(page);
}

/** Invite a member, accept the invitation in a fresh context and enrol MFA. */
export async function inviteMember(
  page: Page,
  browser: Browser,
  email: string,
  role: "approver" | "analyst" | "viewer",
  fullName: string,
): Promise<{ page: Page; context: BrowserContext }> {
  await page.getByRole("link", { name: "Invitations" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Role").selectOption(role);
  await page.getByRole("button", { name: "Invite" }).click();
  await expect(page.getByText(`Invitation created for ${email}`)).toBeVisible();

  const context = await browser.newContext();
  await context.setExtraHTTPHeaders(clientIpHeader());
  const memberPage = await context.newPage();
  await memberPage.goto(`/accept-invite#token=${await tokenFromMail(email)}`);
  await memberPage.getByLabel("Your name").fill(fullName);
  await memberPage.getByLabel("Password").fill(PASSWORD);
  await memberPage.getByRole("button", { name: "Accept invitation" }).click();
  await signIn(memberPage, email);
  await enrolMfa(memberPage);
  return { page: memberPage, context };
}

async function csrfToken(page: Page): Promise<string> {
  return (await page.context().cookies()).find((cookie) => cookie.name === "__Host-csrf")?.value ?? "";
}

/** A real API call from the signed-in browser context (CSRF header included). */
export async function apiJson<T>(
  page: Page,
  method: "GET" | "POST" | "PUT" | "PATCH",
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const response = await page.request.fetch(path, {
    method,
    ...(body === undefined ? {} : { data: body }),
    headers: { "X-CSRF-Token": await csrfToken(page), ...extraHeaders },
  });
  if (!response.ok()) {
    throw new Error(`${method} ${path} failed: ${response.status()} ${await response.text()}`);
  }
  return (await response.json()) as T;
}

/** The status of a real API call, for asserting a server-side 403. */
export async function apiStatus(
  page: Page,
  method: "GET" | "POST" | "PATCH",
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<number> {
  const response = await page.request.fetch(path, {
    method,
    ...(body === undefined ? {} : { data: body }),
    headers: { "X-CSRF-Token": await csrfToken(page), ...extraHeaders },
  });
  return response.status();
}

export async function expectNoA11yViolations(page: Page, name: string): Promise<void> {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const summary = result.violations.map(
    (violation) =>
      `${violation.id} (${violation.nodes.length}): ${violation.nodes
        .slice(0, 3)
        .map((node) => node.target.join(" "))
        .join(" | ")}`,
  );
  expect(summary, `${name}: ${summary.join(", ")}`).toEqual([]);
}

async function poll<T>(
  read: () => Promise<T>,
  done: (value: T) => boolean,
  label: string,
  timeoutMs = 90_000,
): Promise<T> {
  const started = Date.now();
  for (;;) {
    const value = await read();
    if (done(value)) return value;
    if (Date.now() - started > timeoutMs) throw new Error(`timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

/** Polls a job until it succeeds; a failed job throws with the server's last_error instead of timing out. */
async function pollJob(page: Page, jobId: string, label: string): Promise<void> {
  await poll(
    async () => {
      const job = await apiJson<{ status: string; last_error: string | null }>(
        page,
        "GET",
        `/api/v1/jobs/${jobId}`,
      );
      if (job.status === "failed") throw new Error(`${label} failed: ${job.last_error ?? "no error given"}`);
      return job;
    },
    (job) => job.status === "succeeded",
    label,
  );
}

interface RecoveryCase {
  id: string;
  case_type: string;
  reason_code: string;
  status: string;
  deadline_unknown: boolean;
  deadline_date: string | null;
  variance_amount: string;
  recovered_amount: string;
  written_off_amount: string;
  owner_user_id: string | null;
  manufacturer_id: string;
}

interface RecoveryFixture {
  manufacturerId: string;
  cases: RecoveryCase[];
  approverA: Page;
  approverB: Page | null;
  contexts: BrowserContext[];
}

interface GroundManufacturer {
  name: string;
  code: string;
  claim_window_days: number;
  claim_window_basis: string;
  dispute_window_days: number;
  allow_negative_claims: boolean;
  conflict_policy: string;
  variance_tolerance: string;
}

interface CalculationResult {
  id: string;
  document_number: string;
  line_number: number;
  manufacturer_id: string | null;
  status: string;
}

interface Paged<T> {
  data: T[];
  page: { has_more: boolean; next_cursor: string | null };
}

interface MatchRow {
  id: string;
  status: string;
  revision: number;
}

/** The S9 ground truth: the seven lines claimed before CM-7781 is reconciled. */
const CLAIM_FIXTURE = JSON.parse(
  readFileSync(generated("ground_truth/claim_batches_fixture.json"), "utf8"),
) as {
  batch_number: string;
  submitted_at: string;
  lines: { invoice_no: string; line_no: number }[];
};

/** The result ids of the seven ground-truth lines in a completed run (page through the result list). */
async function claimableResultIds(page: Page, runId: string, manufacturerId: string): Promise<string[]> {
  const wanted = new Map(
    CLAIM_FIXTURE.lines.map((line) => [`${line.invoice_no}/${line.line_no}`, line.invoice_no]),
  );
  const found = new Map<string, string>();
  let cursor: string | null = null;
  for (;;) {
    const query = new URLSearchParams({ limit: "200" });
    if (cursor) query.set("cursor", cursor);
    const body = await apiJson<Paged<CalculationResult>>(
      page,
      "GET",
      `/api/v1/calculation-runs/${runId}/results?${query.toString()}`,
    );
    for (const result of body.data) {
      const key = `${result.document_number}/${result.line_number}`;
      if (wanted.has(key) && result.manufacturer_id === manufacturerId && result.status === "eligible") {
        found.set(key, result.id);
      }
    }
    if (!body.page.has_more) break;
    cursor = body.page.next_cursor;
  }
  const missing = [...wanted.keys()].filter((key) => !found.has(key));
  if (missing.length > 0) {
    throw new Error(`eligible results missing for ${missing.join(", ")}`);
  }
  return CLAIM_FIXTURE.lines.map((line) => {
    const id = found.get(`${line.invoice_no}/${line.line_no}`);
    if (!id) throw new Error(`result missing for ${line.invoice_no}/${line.line_no}`);
    return id;
  });
}

async function importFile(page: Page, type: string, relPath: string): Promise<void> {
  await page.goto("/imports/new");
  await page.getByLabel("File to upload").setInputFiles(generated(relPath));
  await page.getByLabel("What the file contains").selectOption({ label: type });
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page).toHaveURL(/\/imports\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  const map = page.getByRole("heading", { name: "Map the columns" });
  const review = page.getByRole("heading", { name: "Check and confirm" });
  await expect(map.or(review)).toBeVisible({ timeout: 120_000 });
  if (await map.isVisible()) {
    await page.getByRole("button", { name: "Apply mapping and check rows" }).click();
    await expect(review).toBeVisible({ timeout: 60_000 });
  }
  await page.getByRole("button", { name: "Confirm import" }).click();
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible({ timeout: 120_000 });
}

/**
 * S8 finalise setup reused for S9: CM-7781 becomes four recovery cases
 * (short_paid 150.00, rejected 2080.00, unmatched_payment 75.00, unpaid 660.00,
 * the last with an unknown deadline).
 */
export async function bootstrapRecovery(
  page: Page,
  browser: Browser,
  options: { twoApprovers?: boolean } = {},
): Promise<RecoveryFixture> {
  const admin = uniqueEmail("e2e-recovery-admin");
  const approverAEmail = uniqueEmail("e2e-recovery-approver-a");
  const approverBEmail = uniqueEmail("e2e-recovery-approver-b");
  const contexts: BrowserContext[] = [];

  await registerWorkspace(page, admin);

  // Master data used by the SPA agreement and the CM-7781 claim lines.
  await page.getByRole("link", { name: "Master data" }).click();
  const manufacturers = JSON.parse(
    readFileSync(generated("ground_truth/master_manufacturers.json"), "utf8"),
  ) as GroundManufacturer[];
  for (const manufacturer of manufacturers) {
    await page.getByLabel("Name", { exact: true }).fill(manufacturer.name);
    await page.getByLabel("Code", { exact: true }).fill(manufacturer.code);
    await page.getByRole("button", { name: "Add manufacturer" }).click();
    await expect(
      page.getByRole("table", { name: "Manufacturers" }).getByText(manufacturer.name),
    ).toBeVisible();
  }

  await page.goto("/master-data/branches");
  await page.getByLabel("Name", { exact: true }).fill("Branch 01 (SYNTHETIC)");
  await page.getByLabel("Code", { exact: true }).fill("BR01");
  await page.getByRole("button", { name: "Add branch" }).click();
  await expect(page.getByText("Branch added.")).toBeVisible();

  for (const [file, type] of [
    ["master/customers.csv", "Customers"],
    ["master/products.csv", "Products"],
    ["master/uom_conversions.csv", "UoM conversions"],
    ["sales/sales_scenarios_layoutA.csv", "Sales"],
  ] as const) {
    await importFile(page, type, file);
  }

  const manufacturerList = await apiJson<{ data: { id: string; code: string }[] }>(
    page,
    "GET",
    "/api/v1/manufacturers?limit=200",
  );
  const voltara = manufacturerList.data.find((manufacturer) => manufacturer.code === "VOLT");
  if (!voltara) throw new Error("VOLT manufacturer missing after master-data setup");

  // The run happens on the real clock (well after the ground-truth invoices), so widen the claim window
  // to keep the seven June lines eligible. Submission-date dispute deadlines make the unmatched payment
  // the one unknown deadline (it has no claim line to count from).
  const groundVolt = manufacturers.find((manufacturer) => manufacturer.code === "VOLT");
  if (!groundVolt) throw new Error("VOLT ground-truth settings missing");
  const currentSettings = await apiJson<{ revision: number }>(
    page,
    "GET",
    `/api/v1/manufacturers/${voltara.id}/settings`,
  );
  await apiJson(
    page,
    "PUT",
    `/api/v1/manufacturers/${voltara.id}/settings`,
    {
      claim_window_days: 250,
      claim_window_basis: groundVolt.claim_window_basis,
      dispute_window_days: groundVolt.dispute_window_days,
      dispute_window_basis: "submission_date",
      allow_negative_claims: groundVolt.allow_negative_claims,
      conflict_policy: groundVolt.conflict_policy,
      variance_tolerance: groundVolt.variance_tolerance,
      payment_lag_days_expected: 30,
      notes: null,
    },
    { "If-Match": `"${currentSettings.revision}"` },
  );

  const approverA = await inviteMember(page, browser, approverAEmail, "approver", "Recovery Approver A");
  contexts.push(approverA.context);
  let approverB: { page: Page; context: BrowserContext } | null = null;
  if (options.twoApprovers) {
    approverB = await inviteMember(page, browser, approverBEmail, "approver", "Recovery Approver B");
    contexts.push(approverB.context);
  }

  // SPA agreement (the calculation engine needs it for the claim lines).
  await page.goto("/agreement-drafts");
  await page.getByRole("button", { name: "New draft" }).click();
  await expect(page).toHaveURL(/\/agreement-drafts\/[0-9a-f-]+$/);
  const draftId = page.url().split("/").pop() ?? "";
  await page.getByLabel("Agreement type").selectOption("spa");
  await page.getByLabel("External number").fill("SPA-REC-2026");
  await page.getByLabel("Manufacturer code").fill("VOLT");
  await page.getByLabel("Name", { exact: true }).fill("Voltara contractor SPA (SYNTHETIC)");
  await page.getByLabel("Valid from").fill("2026-01-01");
  await page.getByLabel("Valid to").fill("2026-12-31");
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "Add customer" }).click();
  }
  for (const [index, code] of ["ALDER", "BLUFF", "CEDAR"].entries()) {
    await page.getByLabel("Customer code").nth(index).fill(code);
  }
  for (const [index, item] of [
    { sku: "BRK-3P-100A", ref: "100.00", dev: "78.00", sell: "88.00" },
    { sku: "BRK-3P-60A", ref: "64.50", dev: "52.00", sell: "58.00" },
    { sku: "PNL-200A", ref: "412.00", dev: "360.00", sell: "389.00" },
  ].entries()) {
    await page.getByRole("button", { name: "Add item" }).click();
    await page.getByLabel(`Item ${index + 1} SKU`).fill(item.sku);
    await page.getByLabel(`Item ${index + 1} reference cost`).fill(item.ref);
    await page.getByLabel(`Item ${index + 1} deviated cost`).fill(item.dev);
    await page.getByLabel(`Item ${index + 1} sell price`).fill(item.sell);
  }
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await page.getByRole("region", { name: "Validation" }).getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validation passed.")).toBeVisible();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Draft submitted for approval.")).toBeVisible();

  await approverA.page.goto(`/agreement-drafts/${draftId}`);
  await expect(approverA.page.getByRole("button", { name: "Approve draft" })).toBeVisible();
  await approverA.page.getByRole("button", { name: "Approve draft" }).click();
  await expect(approverA.page.getByText("Draft approved.")).toBeVisible();

  // Calculation run over the imported 2026 sales.
  await page.getByRole("link", { name: "Calculations" }).click();
  await page.getByLabel("Date from").fill("2026-01-01");
  await page.getByLabel("Date to").fill("2026-12-31");
  await page.getByRole("button", { name: "Start calculation run" }).click();
  const runsTable = page.getByRole("table", { name: "Calculation runs" });
  const firstRunRow = runsTable.locator("tbody tr").first();
  await expect(firstRunRow).toBeVisible({ timeout: 30_000 });
  await expect(firstRunRow.locator("td").nth(1)).toHaveText("Completed", { timeout: 180_000 });

  const runs = await apiJson<{ data: { id: string; status: string }[] }>(
    page,
    "GET",
    "/api/v1/calculation-runs?limit=10",
  );
  const run = runs.data.find((item) => item.status === "completed");
  if (!run) throw new Error("completed calculation run missing");

  // Claim exactly the seven ground-truth lines, then approve and submit as the second person (four-eyes
  // is on by default; the admin created the batch).
  const resultIds = await claimableResultIds(page, run.id, voltara.id);
  const batch = await apiJson<{ id: string }>(
    page,
    "POST",
    "/api/v1/claim-batches",
    { manufacturer_id: voltara.id, result_ids: resultIds },
    { "Idempotency-Key": crypto.randomUUID() },
  );
  await apiJson(approverA.page, "POST", `/api/v1/claim-batches/${batch.id}/approve`);
  await apiJson(
    approverA.page,
    "POST",
    `/api/v1/claim-batches/${batch.id}/submit`,
    {
      submitted_at: CLAIM_FIXTURE.submitted_at,
      external_reference: CLAIM_FIXTURE.batch_number,
    },
    { "Idempotency-Key": crypto.randomUUID() },
  );

  // Upload CM-7781 through the real files screen.
  const remittanceName = "credit_memo_VOLT_CM-7781.csv";
  await page.goto("/files");
  await page.getByLabel("Type").selectOption("remittance");
  await page.getByLabel("File").setInputFiles(generated(`remittance/${remittanceName}`));
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText(`${remittanceName} uploaded`)).toBeVisible({ timeout: 60_000 });

  const files = await poll(
    () =>
      apiJson<{ data: { id: string; original_name: string; scan_status: string }[] }>(
        page,
        "GET",
        "/api/v1/files",
      ),
    (result) =>
      result.data.some((file) => file.original_name === remittanceName && file.scan_status === "clean"),
    "CM-7781 scan",
  );
  const remittanceFile = files.data.find((file) => file.original_name === remittanceName);
  if (!remittanceFile) throw new Error("CM-7781 file id missing");

  const created = await apiJson<{ job: { id: string }; remittance: { id: string } }>(
    page,
    "POST",
    "/api/v1/remittances",
    {
      manufacturer_id: voltara.id,
      file_id: remittanceFile.id,
      channel: "credit_memo",
      doc_type: "credit_memo",
      document_number: "CM-7781",
      document_date: "2026-07-31",
      payment_date: "2026-07-31",
    },
    { "Idempotency-Key": crypto.randomUUID() },
  );

  await pollJob(page, created.job.id, "CM-7781 extraction job");
  await poll(
    () =>
      apiJson<{ lines: unknown[] }>(page, "GET", `/api/v1/remittances/${created.remittance.id}/extraction`),
    (extraction) => extraction.lines.length > 0,
    "CM-7781 extraction lines",
  );

  await apiJson(page, "POST", `/api/v1/remittances/${created.remittance.id}/accept`, {
    corrections: [],
    document_number: "CM-7781",
    document_date: "2026-07-31",
    payment_date: "2026-07-31",
  });
  const reconciled = await apiJson<{ job: { id: string } }>(
    page,
    "POST",
    `/api/v1/remittances/${created.remittance.id}/reconcile`,
  );
  await pollJob(page, reconciled.job.id, "CM-7781 reconciliation job");

  // The unique subset match is a suggestion: a person must confirm it before finalising.
  const lines = await apiJson<{ data: { matches: MatchRow[] }[] }>(
    page,
    "GET",
    `/api/v1/remittances/${created.remittance.id}/reconciliation?limit=200`,
  );
  for (const line of lines.data) {
    for (const match of line.matches) {
      if (match.status !== "suggested") continue;
      await apiJson(page, "POST", `/api/v1/matches/${match.id}/confirm`, undefined, {
        "If-Match": `"${match.revision}"`,
      });
    }
  }

  await apiJson(page, "POST", `/api/v1/remittances/${created.remittance.id}/finalize`, undefined, {
    "Idempotency-Key": crypto.randomUUID(),
  });

  const cases = await poll(
    () => apiJson<{ data: RecoveryCase[] }>(page, "GET", "/api/v1/cases?limit=50"),
    (result) => result.data.length >= 4,
    "four recovery cases",
  );

  // Expected money comes from the ground truth, never from what the UI or API returned.
  const truth = JSON.parse(readFileSync(generated("ground_truth/expected_reconciliation.json"), "utf8")) as {
    expected: { variance?: { amount: string; case_type: string; reason_code: string } | null }[];
  };
  const expectedCases = truth.expected
    .flatMap((line) => (line.variance ? [line.variance] : []))
    .map((v) => `${v.case_type}|${v.reason_code}|${v.amount}`)
    .sort();
  const actualCases = cases.data.map((c) => `${c.case_type}|${c.reason_code}|${c.variance_amount}`).sort();
  expect(actualCases).toEqual(expectedCases);

  return {
    manufacturerId: voltara.id,
    cases: cases.data,
    approverA: approverA.page,
    approverB: approverB?.page ?? null,
    contexts,
  };
}

/** Value cell of the "Status" term on a claim batch detail page. */
export function batchStatus(page: Page) {
  return page.locator("dt", { hasText: /^Status$/ }).locator("xpath=following-sibling::dd");
}
