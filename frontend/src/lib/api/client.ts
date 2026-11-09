import createClient from "openapi-fetch";

import type { paths } from "../../api/schema";

// Same-origin: Vite proxies /api in dev, Caddy in production. Session cookies are HttpOnly (S1).
// `fetch` is looked up per call (not captured at import) so tests and polyfills see the current one.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});
