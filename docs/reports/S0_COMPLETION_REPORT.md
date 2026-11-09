# S0 Foundation and tooling: completion report

> 2026-11-09. Status: **implementation complete and verified; awaiting owner sign-off.** S1 not started.

S0 is built and verified: the full Makefile chain passes locally and on a fresh `git clone`, CI is green on `0538d4d`, and the SPA calls the API through the Vite proxy.

## 1. What was built

- **Backend core:** fail-fast config, structlog JSON logging with request ids, error envelope, clock, UUIDv7 ids, money helpers that reject floats, DB engine with a per-request transaction (`SET LOCAL app.tenant_id`) and a startup check that refuses privileged roles (superuser, owner, BYPASSRLS).
- **API:** app factory, `/health/live`, `/health/ready`, error handlers, request-id middleware.
- **Database:** `init-db.sh` (roles, database, extensions, revokes), Alembic env as owner role, baseline migration, `_rls.py` / `_immutable.py` helpers, schema-lint tests.
- **Quality gates:** ruff, mypy (strict on engine/matching), import-linter, bandit, pip-audit, gitleaks, OpenAPI → TypeScript client generation with a drift check.
- **Frontend:** Vite + React + TypeScript (strict) shell that calls the health endpoint and shows the result.
- **Dev stack:** Compose (postgres, mailpit, api, frontend; clamav behind profile `s2`), multi-stage Dockerfiles, Makefile, CI with 5 jobs.

## 2. Bugs found and fixed during Docker verification

| Commit | Problem | Fix |
|---|---|---|
| `a553a42` | `make up` failed on a fresh volume: the API's startup role check needs `rebate_app`, which `db-init` had not created yet | `make up` starts Postgres, runs `db-init`, then starts the rest |
| `1d4fdbd` | `make env` failed on Windows: `python3` is the Microsoft Store stub | Uses `python` on Windows, `python3` elsewhere; override with `PYTHON=` |
| `c7f014b` | `infra/runbooks/` missing | Added with a pointer to `DEPLOYMENT.md` §7.1; runbook content is S11 work |

## 3. Testing

| Check | Result |
|---|---|
| `make up / db-init / migrate / test / test-isolation / lint / check-client / down` | All pass |
| Fresh `git clone` → `make env` → `up` → `db-init` → `migrate` → `test` | Pass: 59 backend tests (94.6% coverage), 3 frontend tests |
| Planted violation (`rebate_engine` imports `rebate.core.db`) | `import-linter` fails as expected; file restored |
| SPA → `/api/v1/health/live` via the :5173 proxy | curl returns 200; headless Chrome renders `API: live` |
| Images | Runtime image runs as non-root (UID 10001); frontend dev container runs as `node` |
| CI (`dccf862`, `0538d4d`) | All 5 jobs pass: backend, frontend, api-contract, secrets, images |

## 4. Code review checklist (`CODE_REVIEW_CHECKLIST.md`)

| Section | Result |
|---|---|
| 1. Functionality | Pass. Nothing mocked or faked; startup failure paths (bad config, privileged role) tested |
| 2. Code quality | Pass. All linters and type checks green; no dependencies added this session |
| 3. Security | Pass for S0 scope; gitleaks clean. Auth, permissions and CSRF: not applicable until S1 |
| 4. Database | Pass. Empty → head migration works on a fresh volume; schema lint runs with zero tenant tables |
| 5. Financial | Not applicable beyond the money helper's float-rejection test (passes) |
| 6. Errors | Pass. Error envelope and request ids covered by tests |
| 7. Testing | Pass. Real PostgreSQL; CI fails on skipped tests (shown earlier with a probe PR) |
| 8. Performance | Not applicable to S0 |
| 9. Frontend | Generated client, no drift. **Accessibility (axe) check not run** |
| 10. Documentation | Pass. `PROJECT_STATUS.md` and `IMPLEMENTATION_PLAN.md` updated |
| 11. Regression | Not applicable (first stage) |

## 5. Deviations, known issues, decision needed

- **Dev API container runs as root** (it bind-mounts the source). This matches `DEPLOYMENT.md` §2.5, which requires non-root only for the runtime image.
- **Existing warnings:** K-7 (Starlette deprecates `httpx` in its test client; tests only), K-8 (minimatch advisory in ESLint tooling; dev only, version pinned).
- **This machine:** a local Postgres holds port 5432, so `.env` uses `POSTGRES_HOST_PORT=5433`, as the README suggests.
- **Decision needed:** owner sign-off on S0, to start the S1 kickoff report.
