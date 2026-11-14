# Dependency and licence audit, 2026-11-14

Scope: `origin/main` at b4a0e80 (after the S7-S10 integration and UI merges). No lockfile or manifest was changed.

## Commands and results

| Check | Command (as CI runs it) | Result |
|---|---|---|
| Python advisories | `uv export --frozen --no-hashes --no-emit-project` then `pip-audit --strict --no-deps --disable-pip -r` (api container, once) | No known vulnerabilities found |
| npm advisories, runtime | `npm ci; npm audit --omit=dev` (`frontend/`) | found 0 vulnerabilities |
| npm advisories, all | `npm audit` | found 0 vulnerabilities |
| Python licences (runtime) | `uv export --no-dev ...` then `infra/scripts/ops/licence_check.py python` (same container run) | 60 packages checked, 0 blocking |
| npm licences (runtime) | `infra/scripts/ops/licence_check.py npm package-lock.json` | 57 packages checked, 0 blocking |

Licence notes (Python, weak copyleft, listed for review, not blocking): certifi (MPL-2.0), psycopg and psycopg-binary (LGPL-3.0-only; used as an unmodified dynamically linked library). Skipped as not installed on this platform: httpx2-jsfetch, tzdata (tzdata is Apache-2.0; httpx2-jsfetch is a Pyodide-only transitive package, not shipped).

## Advisories

None found in any scan, so there is nothing to fix, accept or escalate. Known earlier item K-8 (minimatch, dev only) remains handled by the npm `overrides` pin.

## Open Dependabot PRs: decided not merged for Release 1

| PR | Content | Decision | Reason |
|---|---|---|---|
| #27 | docker group: Python 3.14 to 3.15, Node 24 to 26 | Not merged | Contradicts the ADR-027 toolchain pins; a runtime-version change needs its own decision and a full gate |
| #28 | npm group, 8 updates incl. React 18 to 19 and TypeScript 5 to 7 | Not merged | Majors; contradict `TECH_STACK.md` (React 18, TypeScript 5); would need a migration stage |
| #29 | python group, 5 updates incl. gunicorn 26 to 27 | Not merged | A major version is bundled with minors; split into a minor-only update after review (owner/builder, post Release 1) |

## Unverified

The licence check for Python reads installed metadata, so it ran inside the api image (with the repo `infra/` mounted read only) rather than on the host. CI runs `pip-audit` against the lockfile only; advisories published after 2026-11-14 are not covered.
