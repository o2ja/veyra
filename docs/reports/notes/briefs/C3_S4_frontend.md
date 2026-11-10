# C3 — S4 agreements frontend

Agent id: `c3-s4-frontend`. Branch: `s4-frontend`. No backend changes (S4 backend is on main); report API gaps.

## Read
`COMMON.md`; `CLAUDE.md` §10; `IMPLEMENTATION_PLAN.md` §S4 (S4.8, S4.9 W5/W7); `PRODUCT_REQUIREMENTS.md` §1.5 + UX
parts; `API_SPECIFICATION.md` (agreements, drafts, approval, versions/diff, If-Match); `TESTING_STRATEGY.md`
(frontend/e2e). Patterns: `frontend/src/features/{files,admin,masterdata,imports}/*`, `app/routes.tsx`, the
generated client in `frontend/src/api`, `frontend/e2e/*.spec.ts` (fixtures, axe).

## Build (S4.8)
Agreements list/detail; draft editor (header, rules, customers, items grid virtualised, tiers); validation panel
(errors vs warnings, unresolved references); submit (acknowledgements) / approve / reject / return with four-eyes
shown honestly (approve disabled for the author, server is authoritative); version history + field-level diff;
bulk items import (reuses the import wizard). If-Match conflicts shown with a reload path. Permission-aware
navigation, keyboard accessible, generated client only.

## Tests
Vitest for non-trivial logic (diff rendering, editor state); Playwright W5 and W7 (manual path) against the real
backend with `testdata/generated/agreements/manual_payloads/*`, axe checks on every new page.

## You own
`frontend/src/features/agreements/**`, route/nav lines, `frontend/e2e/agreements*.spec.ts`,
`docs/reports/notes/c3-s4-frontend.md`.
