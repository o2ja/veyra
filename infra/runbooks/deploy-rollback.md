# Deploy and rollback (single Docker host behind Cloudflare)

Status: **written, not executed** (no host exists). Compose file validated in CI with `docker compose config`.
`C` below is `docker compose --env-file .env.prod -f infra/docker/compose.prod.yml`.

## First-time host setup (operator, once)

1. Host: Docker + Compose, 80/443 allowed only from Cloudflare ranges at the provider firewall or in the
   `DOCKER-USER` iptables chain (ufw does NOT filter Docker-published ports; `runbooks/cloudflare-ip-update.md`), SSH key-only, unattended security updates.
2. `cp .env.prod.example .env.prod; chmod 0600 .env.prod`; fill every `replace-me` with generated, URL-safe values.
   Set `CLOUDFLARE_IP_RANGES` from the current Cloudflare lists. Owner keeps an offline copy of `ENCRYPTION_KEYS`.
3. Cloudflare (dashboard, operator): proxy `APP_DOMAIN` and `S3_DOMAIN`, SSL mode Full (strict), optionally
   Authenticated Origin Pulls. Caddy gets its origin certificate by ACME, or install a Cloudflare Origin CA
   certificate in Caddy if HTTP-01 is blocked by the origin firewall.
4. `$C up -d postgres`, then `$C exec -T -e PGUSER=postgres -e REBATE_OWNER_PASSWORD -e REBATE_APP_PASSWORD -e REBATE_AI_WRITER_PASSWORD -e REBATE_READONLY_PASSWORD postgres bash /opt/rebate-scripts/init-db.sh`
   (export those variables from `.env.prod` first; the script reads them from the exec environment).
5. Provision the bucket with the separate admin identity (the app identity is limited to the one bucket and cannot
   create it): `$C up -d seaweedfs`, then
   `S3_ACCESS_KEY_ID=<S3_ADMIN_ACCESS_KEY_ID> S3_SECRET_ACCESS_KEY=<S3_ADMIN_SECRET_ACCESS_KEY> $C run --rm --no-deps -T -e S3_ACCESS_KEY_ID -e S3_SECRET_ACCESS_KEY api python -c "$(cat infra/scripts/ops/s3_tar.py)" init`.
   SeaweedFS in production is an interim choice pending owner decision OD-3/OD-4.
6. Continue with "Deploy" below.

## Deploy

0. `python3 infra/scripts/check-proxy-config.py .env.prod infra/docker/compose.prod.yml` must print OK (the
   trusted proxy IP must equal Caddy's fixed internal address); never `up` without it.
1. Take a backup first: `infra/scripts/ops/backup.sh`. Note the currently running tag: `grep REBATE_IMAGE_TAG .env.prod`.
2. Build images from the release commit (`git checkout <sha>`), set `REBATE_IMAGE_TAG=<sha>` in `.env.prod`,
   `$C build api caddy` (worker uses the api image).
3. Migrate with owner credentials in a one-off container: `$C --profile ops run --rm migrate` (prints the new head).
4. Start: `$C up -d --remove-orphans` (api, worker, caddy restart on the new image; `depends_on` waits for health).
5. Smoke tests: `curl -fsS https://$APP_DOMAIN/api/v1/health/ready`; log in as a test admin; open a list page;
   `$C ps` shows every service `healthy`; `$C logs --since 5m api worker` has no errors.
6. Record the release (tag, time, who) in the ops log; `git tag release-<date>-<sha>`.

## Rollback

Schema changes are expand/contract: the previous release runs on the new schema for one release.

1. Set `REBATE_IMAGE_TAG` back to the previous tag (images are kept locally; rebuild from the old commit if pruned).
2. `$C up -d api worker caddy`; run the smoke tests.
3. If the deploy ran a migration that the old code cannot tolerate (a contract step shipped early), do NOT run
   `alembic downgrade` on production. Either fix forward, or restore the pre-deploy backup into a fresh database
   (`runbooks/backup-restore.md`) and set `REBATE_DB_NAME` in `.env.prod` to it. This loses writes made since the backup:
   decide with the owner and say so in the incident notes.
