# Common brief for every build agent (Veyra Recover, S3–S6 batch)

You are one of several agents building stages S3–S6 in parallel. An orchestrator integrates your branch.
Owner instruction: "Do it RIGHT: keep every test and review. Avoid only waste."

## Rules (non-negotiable)
- Read `CLAUDE.md` first (all of it), then the docs your brief lists. The docs are the source of truth.
- CLAUDE.md's 15 rules apply. In particular: no mocks, fake data, stubs or placeholder logic for required
  behaviour; no floats for money; tenant isolation (RLS ENABLE+FORCE via the existing migration helper) and
  server-side authorization on every new table/route; every DB change is an Alembic migration.
- If something cannot be finished properly, say so in your report. Do not stub it, do not fake it.
- Expected values in tests come from `docs/BUSINESS_LOGIC.md`, `testdata/generated/ground_truth/*` or an
  independent hand derivation, **never** from the output of the code under test.
- Follow existing patterns. Look at `modules/files`, `modules/identity`, `core/jobs.py`, `core/db.py`,
  `tests/isolation/*`, migrations `20261110_1100_files_jobs.py` before writing similar code.
- Edit only the files your brief says you own. If you must change something outside, stop and say so in the
  report (do not do it), unless the brief allows it.
- Do not tick boxes in `docs/IMPLEMENTATION_PLAN.md` and do not edit `docs/PROJECT_STATUS.md` (the orchestrator does).
  Doc changes outside your owned docs go into `docs/reports/notes/<your-agent-id>.md` (deviations, ADR text,
  doc deltas). The orchestrator merges them.

## Environment (Windows host, Git Bash; Docker services are SHARED with other agents)
- You work in your own git worktree on your own branch. First step: `cp /d/veyra/rebate-recovery-docs/rebate-recovery/.env .env`
  (gitignored; never commit or print it).
- Postgres, Mailpit, SeaweedFS, ClamAV, api, worker and frontend containers are already running
  (compose project `veyra-recover`). **Never** run `docker compose up/down/build/restart/stop`, never rebuild
  images, never touch the dev database `rebate`. If you need a new dependency, stop and report it.
- Backend commands run in a one-off container that mounts YOUR worktree's `backend/` (compose paths are relative):
  ```sh
  set -a; . ./.env; set +a
  DC="docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2"
  ADMIN="postgresql+psycopg://postgres:$POSTGRES_PASSWORD@postgres:5432/postgres"
  $DC run --rm --no-deps -T -e POSTGRES_PASSWORD -e REBATE_OWNER_PASSWORD -e REBATE_APP_PASSWORD \
     -e REBATE_TEST_DB=<your test db name> -e TEST_ADMIN_DATABASE_URL="$ADMIN" api sh -c 'pytest -x -q --no-cov tests/<path>'
  ```
  Always pass your own `REBATE_TEST_DB` (given in your brief) so parallel agents do not collide.
  Lint gates: the `lint` target in `Makefile` (run its commands the same way; gitleaks runs on the host).
  Frontend: `$DC run --rm --no-deps -T frontend sh -c 'npm test'` (and `npm run lint`, `npm run typecheck`).
  Mailpit is shared: a mail assertion that fails because another agent's mail arrived is a test-design bug;
  filter by your recipient address.
- Do not run `alembic` on the host.
- The isolation negative control uses the fixed database `rebate_negctl`; run it only if your brief says so.

## Working method (avoid waste)
- While developing, run only the tests for the code you just changed. Run your FULL backend suite (and
  frontend suite if you touched it) once when your work is complete, plus lint gates. Never run the full suite
  twice on unchanged code.
- Commit in small, tested steps on your branch with clear messages ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- When complete: `git push -u origin <branch>` and open a PR to `main` with `gh pr create --fill` (body ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`; never write `@name` mentions). Watch CI with
  `gh pr checks --watch` and fix red builds. Do NOT merge.
- No progress narration. One final report.

## Final report (your last message, at most ~80 lines, plain facts)
1. Branch, PR URL, final commit SHA, CI status (per job).
2. What was built (files/modules, migrations with revision ids and down_revision).
3. Tests: exact commands run and pass/fail counts; which acceptance criteria each test covers.
4. NOT done / not tested / limitations (be honest; "not tested" when not tested).
5. Deviations from docs and anything you needed outside your owned files.
6. Interfaces other agents depend on (function signatures, tables, endpoints) — short.
