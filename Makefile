# Developer commands (DEPLOYMENT.md §2.2). Needs Docker and a POSIX shell
# (Linux/macOS: any shell; Windows: run from Git Bash). Secrets come from .env (see `make env`).
ifneq (,$(wildcard .env))
include .env
export
endif

# Windows: `python3` is usually the Microsoft Store stub. Override with `make env PYTHON=...`.
ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif

COMPOSE := docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2
API_RUN := $(COMPOSE) run --rm --no-deps -T -e POSTGRES_PASSWORD -e REBATE_OWNER_PASSWORD -e REBATE_APP_PASSWORD -e REBATE_AI_WRITER_PASSWORD api
FE_RUN := $(COMPOSE) run --rm --no-deps -T frontend
ADMIN_URL := postgresql+psycopg://postgres:$$POSTGRES_PASSWORD@postgres:5432/postgres
OWNER_URL := postgresql+psycopg://rebate_owner:$$REBATE_OWNER_PASSWORD@postgres:5432/rebate

.PHONY: env up down db-init migrate test test-backend test-frontend test-isolation lint gen-client check-client ci-local

env: ## create .env with generated local-only secrets (refuses to overwrite)
	$(PYTHON) infra/scripts/make-env.py

up: ## start postgres, mailpit, seaweedfs, clamav, api, worker, frontend
	$(COMPOSE) up -d --wait postgres
	$(MAKE) db-init # api's startup role check needs rebate_app to exist
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

db-init: ## roles, database, extensions (idempotent)
	$(COMPOSE) exec -T -e PGUSER=postgres postgres bash /opt/rebate-scripts/init-db.sh

migrate: ## alembic upgrade head as rebate_owner (one-off container)
	$(API_RUN) sh -c 'export DATABASE_URL_OWNER="$(OWNER_URL)"; alembic upgrade head && alembic current'

test: test-backend test-frontend

test-backend: ## full backend suite against a fresh rebate_test database
	$(API_RUN) sh -c 'TEST_ADMIN_DATABASE_URL="$(ADMIN_URL)" pytest'

test-frontend:
	$(FE_RUN) npm test

test-isolation: ## tenant-isolation suite + schema meta-test + negative control (RLS off in a throwaway DB)
	$(API_RUN) sh -c 'export TEST_ADMIN_DATABASE_URL="$(ADMIN_URL)"; pytest --no-cov tests/isolation tests/db/test_schema_lint.py tests/db/test_migration_helpers.py && python -m tests.isolation.negative_control'

lint: ## all static gates (backend, frontend, secrets)
	$(API_RUN) sh -c 'ruff check . && ruff format --check . && mypy && lint-imports --config importlinter.ini && bandit -q -c pyproject.toml -r src migrations && uv export --frozen --no-hashes --no-emit-project -o /tmp/req.txt -q && pip-audit --strict --no-deps --disable-pip -r /tmp/req.txt'
	$(FE_RUN) sh -c 'npm run lint && npm run typecheck && npm audit --audit-level=moderate'
	gitleaks git --no-banner --redact .

gen-client: ## regenerate frontend/src/api from the backend OpenAPI document
	$(API_RUN) python -m rebate.api > frontend/src/api/openapi.json
	$(FE_RUN) npm run gen:api

check-client: gen-client ## CI-style drift check
	git diff --exit-code -- frontend/src/api

ci-local: ## the six CI jobs locally on the current commit; stops at the first failure (owner rule 2026-11-13)
	bash infra/scripts/ci-local.sh
