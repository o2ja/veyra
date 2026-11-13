#!/usr/bin/env bash
# `make ci-local`: the six CI jobs (.github/workflows/ci.yml) on this machine, against the dev stack.
# Run from the root of the worktree whose commit you want to gate (Git Bash on Windows).
# Same steps as CI, in the same order inside each job; stops at the first failure; prints pass/fail per job.
# Differences from CI (environment only): services come from compose.dev.yml instead of runner services;
# migrations are checked on a throwaway database `rebate_ci`; the backend suite uses REBATE_TEST_DB=rebate_test_ci;
# Playwright uses the installed Chrome (PW_CHANNEL=chrome) and runs in batches of 4 specs with the dev
# rate-limit counters cleared between batches (dev only; limits are never disabled).
# ponytail: uses the already-built dev images; rebuild them (`make up`) when a branch changes uv.lock or package-lock.json.
set -uo pipefail
export MSYS_NO_PATHCONV=1

ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$ROOT" || exit 1
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ci-local: working tree has uncommitted changes; commit first so the result belongs to one SHA"; exit 1
fi
# one full run at a time on this machine (owner rule: never two full suites at once)
LOCK="$(git rev-parse --git-common-dir)/ci-local.lock"
mkdir "$LOCK" 2>/dev/null || { echo "ci-local: another run holds $LOCK (remove it if that run is dead)"; exit 1; }
trap 'rm -rf "$LOCK"' EXIT

MAIN_ROOT=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
[ -f .env ] || cp "$MAIN_ROOT/.env" .env || { echo "ci-local: no .env (run make env in $MAIN_ROOT)"; exit 1; }
set -a; . ./.env; set +a
WROOT=$(pwd -W 2>/dev/null || pwd)   # docker on Windows wants D:/... paths for -v

DC="docker compose --env-file .env -f infra/docker/compose.dev.yml --profile s2"
ADMIN="postgresql+psycopg://postgres:$POSTGRES_PASSWORD@postgres:5432/postgres"
API="$DC run --rm --no-deps -T -e POSTGRES_PASSWORD -e REBATE_OWNER_PASSWORD -e REBATE_APP_PASSWORD -e REBATE_AI_WRITER_PASSWORD -e REBATE_READONLY_PASSWORD -v $WROOT/infra:/infra:ro -v $WROOT/testdata:/testdata:ro api"
FE="$DC run --rm --no-deps -T -v veyra-ci-node-modules:/app/node_modules frontend"   # own node_modules: npm ci never touches the dev server's
PSQL="$DC exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres"
PY=${PYTHON:-python}

declare -a RESULTS=()
report() {
  echo; echo "== ci-local $SHA"
  for r in "${RESULTS[@]}"; do echo "  $r"; done
}
run_job() {  # run_job <name> <function>
  echo; echo "=== job: $1"
  if "$2"; then RESULTS+=("PASS  $1"); else RESULTS+=("FAIL  $1"); report; echo "ci-local: FAILED at $1"; exit 1; fi
}
step() { echo "--- $*"; "$@"; }

job_secrets() {
  step gitleaks git --no-banner --redact -v .
}

job_api_contract() {
  step $FE npm ci --no-fund &&
  echo "--- regenerate OpenAPI + client" &&
  $API python -m rebate.api > frontend/src/api/openapi.json &&
  step $FE npm run gen:api &&
  step git diff --exit-code -- frontend/src/api
}

job_backend() {
  $PSQL -qc "DROP DATABASE IF EXISTS rebate_ci WITH (FORCE)" &&
  step $DC exec -T -e PGUSER=postgres -e REBATE_DB=rebate_ci postgres bash /opt/rebate-scripts/init-db.sh &&
  step $DC exec -T -e PGUSER=postgres -e REBATE_DB=rebate_ci postgres bash /opt/rebate-scripts/init-db.sh &&
  step $API sh -c 'export DATABASE_URL_OWNER="postgresql+psycopg://rebate_owner:$REBATE_OWNER_PASSWORD@postgres:5432/rebate_ci"; alembic upgrade head && alembic check' &&
  $PSQL -qc "DROP DATABASE rebate_ci WITH (FORCE)" &&
  step $API sh -c 'set -e
    ruff check .; ruff format --check .; mypy; lint-imports --config importlinter.ini
    bandit -q -c pyproject.toml -r src migrations
    uv export --frozen --no-hashes --no-emit-project -o /tmp/req.txt -q
    pip-audit --strict --no-deps --disable-pip -r /tmp/req.txt
    python /infra/scripts/ops/test_licence_check.py
    uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/req-runtime.txt -q
    python /infra/scripts/ops/licence_check.py python /tmp/req-runtime.txt' &&
  step $API sh -c "TEST_ADMIN_DATABASE_URL='$ADMIN' REBATE_TEST_DB=rebate_test_ci pytest --junitxml=junit-backend.xml" &&
  step $PY infra/scripts/check-no-skips.py backend/junit-backend.xml &&
  step $API sh -c "TEST_ADMIN_DATABASE_URL='$ADMIN' python -m tests.isolation.negative_control" &&
  step $PY -I testdata/verify_business_logic_examples.py
}

job_frontend() {
  step $FE sh -c 'set -e; npm ci --no-fund; npm run lint; npm run typecheck
    npx vitest run --reporter=default --reporter=junit --outputFile.junit=junit-frontend.xml' &&
  step $PY infra/scripts/check-no-skips.py frontend/junit-frontend.xml &&
  step $FE sh -c 'set -e; npm run build; npm audit --audit-level=moderate; npm audit --omit=dev --audit-level=high' &&
  step $PY infra/scripts/ops/licence_check.py npm frontend/package-lock.json
}

job_images() {
  step docker build --target runtime -t rebate-api:ci backend &&
  step test "$(docker run --rm --entrypoint id rebate-api:ci -u)" = "10001" &&
  step docker run --rm --entrypoint python rebate-api:ci -c "import rebate.api.app, uvicorn.workers" &&
  step docker build --target static -t rebate-frontend:ci frontend &&
  step docker compose --env-file .env.prod.example -f infra/docker/compose.prod.yml --profile ops config -q &&
  step $PY infra/scripts/check-proxy-config.py .env.prod.example infra/docker/compose.prod.yml &&
  step docker run --rm -e APP_DOMAIN=localhost -e S3_DOMAIN=files.localhost -e S3_BUCKET=rebate-files \
    -v "$WROOT/infra/caddy:/etc/caddy:ro" \
    caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee \
    caddy validate --config /etc/caddy/Caddyfile
}

job_e2e() {
  # api, worker and the Vite server are recreated from THIS worktree, and the dev DB is migrated to its head.
  step $DC up -d --wait postgres mailpit seaweedfs clamav &&
  step $DC up -d --wait --force-recreate api worker frontend &&
  step $API sh -c 'export DATABASE_URL_OWNER="postgresql+psycopg://rebate_owner:$REBATE_OWNER_PASSWORD@postgres:5432/rebate"; alembic upgrade head' &&
  (cd frontend && step npm ci --no-fund) || return 1
  local specs=(frontend/e2e/*.spec.ts) reports=() i=0
  rm -f frontend/junit-e2e-*.xml
  while [ $i -lt ${#specs[@]} ]; do
    $PSQL -d rebate -qc "DELETE FROM rate_limit_counters" || return 1
    local batch=("${specs[@]:$i:4}") n=$((i / 4))
    echo "--- playwright batch $n: ${batch[*]#frontend/}"
    (cd frontend && PW_CHANNEL=chrome npx playwright test "${batch[@]#frontend/}" && mv junit-e2e.xml "junit-e2e-$n.xml") || return 1
    reports+=("frontend/junit-e2e-$n.xml"); i=$((i + 4))
  done
  step $PY infra/scripts/check-no-skips.py "${reports[@]}"
}

echo "ci-local: $SHA ($(git rev-parse --abbrev-ref HEAD)) in $ROOT"
step $DC up -d --wait postgres mailpit seaweedfs clamav || exit 1
run_job secrets job_secrets
run_job api-contract job_api_contract
run_job frontend job_frontend
run_job backend job_backend
run_job images job_images
run_job e2e job_e2e
report
echo "ci-local: 6/6 PASS at $SHA"
echo "note: api/worker/frontend now run from $ROOT; run 'make up' in $MAIN_ROOT to point the dev stack back at main"
