// Email links carry tokens in the URL fragment (#token=...), which browsers never send to a server.
// Read it once, then remove it from the address bar and history so it cannot leak later.
// Cached per path because React StrictMode runs initialisers twice in development.

let cached: { path: string; token: string | null } | null = null;

export function takeFragmentToken(): string | null {
  const path = window.location.pathname;
  if (cached?.path === path && !window.location.hash) return cached.token;
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const raw = params.get("token");
  if (window.location.hash) {
    window.history.replaceState(null, "", path + window.location.search);
  }
  const token = raw && /^[A-Za-z0-9_-]{20,128}$/.test(raw) ? raw : null;
  cached = { path, token };
  return token;
}
