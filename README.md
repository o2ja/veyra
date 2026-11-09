# Veyra Recover

**Veyra Recover** is a product of **Veyra AI**: rebate and claim recovery for wholesale distributors.

Multi-tenant SaaS that helps distributors find, claim and recover money owed by manufacturers: SPA / ship-and-debit claims, volume rebates, remittance reconciliation and a recovery queue. Calculations are deterministic and traceable; AI only assists with reading documents and never decides eligibility or amounts.

> **Status:** Stage S0 (foundation and tooling) in progress. See `docs/PROJECT_STATUS.md`.

## Start here

| If you are… | Read |
|---|---|
| The owner | `docs/PROJECT_OVERVIEW.md`, then `docs/PROJECT_STATUS.md` (decisions needed), `docs/DEVELOPMENT_ROADMAP.md` |
| Claude Code | `CLAUDE.md` first, then the document map inside it |
| A reviewer | `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_DECISIONS.md`, `docs/SECURITY.md` |

## Contents

```
CLAUDE.md                   permanent instructions for Claude Code (15 strict rules, process, conventions)
docs/
  PROJECT_OVERVIEW.md       vision, goals, assumptions, open decisions, document map
  PRODUCT_REQUIREMENTS.md   requirements, user stories, business rules
  TECH_STACK.md             approved and rejected technology
  ARCHITECTURE.md           components, modules, workflows
  ARCHITECTURE_DECISIONS.md ADR-001..026
  DATABASE_SCHEMA.md        tables, RLS, ER diagram, migrations
  API_SPECIFICATION.md      REST conventions and endpoints
  BUSINESS_LOGIC.md         calculation, reconciliation, deadline rules with worked examples
  AI_PROCESSING.md          extraction pipeline and safeguards
  SECURITY.md               authN/authZ, tenant isolation, uploads, audit
  TESTING_STRATEGY.md       test layers and critical edge cases
  TEST_DATA_SPEC.md         synthetic dataset specification
  DEPLOYMENT.md             environments, CI/CD, backups
  DEVELOPMENT_ROADMAP.md    stages S0–S11 and delivery process
  IMPLEMENTATION_PLAN.md    checkbox task lists per stage
  PROJECT_STATUS.md         current state, decisions, known issues
  CODE_REVIEW_CHECKLIST.md  end-of-stage review
testdata/                   SYNTHETIC generator, verifier, generated files and ground truth
```

## Synthetic test data

All files in `testdata/generated/` are **fabricated** and labelled as such.

```bash
python3 -I testdata/verify_business_logic_examples.py     # independent check of the worked examples
python3 -I testdata/generate.py --volume 2000 [--out DIR] # regenerate (needs openpyxl, reportlab)
```

## Run it locally

Prerequisites: Docker with Compose, GNU make, a POSIX shell (on Windows use Git Bash), Python 3.14 with `uv`, Node 24 LTS (details: `docs/DEPLOYMENT.md` §2). Tests and tools run inside the containers.

```bash
make env        # creates .env with random local-only secrets (edit POSTGRES_HOST_PORT if 5432 is taken)
make up         # postgres, mailpit, api (:8000), frontend (:5173)
make db-init    # roles, database, extensions (idempotent)
make migrate    # alembic upgrade head as rebate_owner
make test       # backend (fresh rebate_test DB) + frontend tests
make lint       # ruff, mypy, import-linter, bandit, pip-audit, ESLint, tsc, npm audit, gitleaks
```

Open http://localhost:5173: the shell shows "API: live" once the API answers through the Vite proxy.
`uv run --directory backend pre-commit install` enables the ruff and gitleaks hooks.

> These instructions are **not yet verified on a clean checkout** (S0.6 is pending Docker on the development machine). CI (`.github/workflows/ci.yml`) runs the same gates on every push to `main` and every pull request.

## How development will run

One stage at a time (S0–S11): kickoff report → owner approval → implement → test and verify → code-review checklist → completion report → owner approval. Local development uses Docker Compose with a fresh PostgreSQL; instructions will be verified in stage S0 (`docs/DEPLOYMENT.md` §2).

## Decisions awaiting the owner

OD-1 first vertical (electrical recommended), OD-2 LLM provider and data terms, OD-3 production Postgres hosting, OD-4 hosting provider/region, OD-5 Release 2 scope. Details in `docs/PROJECT_STATUS.md`.
