import { createHmac, randomBytes } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

// Every spec imports this file, so this hook runs before each test: each test gets its own client IP
// (a random private address, forwarded by the dev proxy, which CI trusts) so per-IP limits such as register (5/h) do not leak across specs.
test.beforeEach(async ({ context }) => {
  const n = randomBytes(2);
  await context.setExtraHTTPHeaders({ "X-Forwarded-For": `10.${n[0] ?? 0}.${n[1] ?? 0}.1` });
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
  return { secret, codes };
}

export async function signIn(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}
