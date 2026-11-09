import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "../../api/schema";

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const CSRF_COOKIE = "__Host-csrf";

export function readCsrfCookie(): string | null {
  for (const part of document.cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === CSRF_COOKIE) return decodeURIComponent(rest.join("="));
  }
  return null;
}

/** Make sure a CSRF cookie exists before the first mutating request (pre-session token). */
export async function ensureCsrf(): Promise<void> {
  if (readCsrfCookie()) return;
  await globalThis.fetch(
    new Request(`${window.location.origin}/api/v1/auth/csrf`, { credentials: "same-origin" }),
  );
}

// Double-submit CSRF (SECURITY.md §1): the cookie is readable, the session cookie is HttpOnly.
const csrf: Middleware = {
  async onRequest({ request }) {
    if (!UNSAFE.has(request.method)) return request;
    await ensureCsrf();
    const token = readCsrfCookie();
    if (token) request.headers.set("X-CSRF-Token", token);
    return request;
  },
};

// Same-origin: Vite proxies /api in dev, Caddy in production. Session cookies are HttpOnly.
// `fetch` is looked up per call (not captured at import) so tests and polyfills see the current one.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});
api.use(csrf);
