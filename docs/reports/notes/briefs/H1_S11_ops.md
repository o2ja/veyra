# H1 — S11 ops code: deployment config, Cloudflare client IP, backup/restore, runbooks, dependency audit
(branch `s11-ops`, test db `rebate_test_h1`)
Read COMMON.md, then ONLY: CLAUDE.md, DEPLOYMENT.md, SECURITY.md §12–§13 (+ the client-IP / rate-limit parts),
TESTING_STRATEGY.md §9–§10, `infra/` (caddy, docker, scripts, runbooks), `backend/src/rebate/core/{clientip,config}.py`,
`backend/src/rebate/api/deps.py` (client_ip use), `.github/workflows/ci.yml` (structure only).
Scope (owner): a single Docker host behind Cloudflare. NOT in scope: real hosting, spending, accounts, DNS, a live
deploy, pen test. Nothing may contact an external service except CI's normal package downloads.
Build in this order, commit + push after each:
1. Client IP behind Cloudflare: honour `CF-Connecting-IP` ONLY when the TCP peer is a trusted proxy (Caddy) AND the
   request came through Cloudflare (configurable list of Cloudflare ranges as a setting, no runtime fetching; doc where
   the list comes from and how to update it); otherwise current X-Forwarded-For logic. Unit + API tests: spoofed
   header from an untrusted peer ignored; valid one used for rate limits and audit; malformed value falls back.
   Caddy config: only accept origin traffic as configured (document Cloudflare authenticated origin pulls/firewall as an
   operator step), pass the header through.
2. Production compose file `infra/docker/compose.prod.yml` (api, worker, postgres, object store, clamav, caddy; no dev
   tools, no exposed DB port, read-only FS where possible, healthchecks, restart policies, secrets from env file
   only) + `.env.prod.example` (no real values). CI job `images` (or a new step) validates it with `docker compose config`.
3. Backup/restore: `infra/scripts/ops/backup.sh` (pg_dump custom format + object store copy, timestamped, checksum),
   `restore.sh` (into a fresh database, verifies checksum, runs migrations check), and an executed, timed restore drill
   against the dev stack into a throwaway database (record the timing in the runbook). WAL/PITR: document the config
   and runbook steps (no hosting exists to enable it).
4. Runbooks in `infra/runbooks/`: deploy/rollback, backup/restore (with the drill timing), secret rotation, incident
   (tenant data exposure suspicion), Cloudflare IP list update.
5. Dependency audit: `pip-audit` + `npm audit --omit=dev` in CI (fail on high/critical), licence check script listing
   any copyleft licence in runtime deps; record current results in `docs/reports/notes/h1.md`.
Own only `infra/**`, `core/clientip.py`, the client-IP setting in `core/config.py`, the related tests, `.github/workflows/ci.yml`
(new steps only), `.env.prod.example`, DEPLOYMENT.md sections you implement. Report what is configuration only and
what was executed.
