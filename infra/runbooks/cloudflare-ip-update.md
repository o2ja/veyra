# Cloudflare IP range list (update and verify)

Why it matters: the API takes the client address from `CF-Connecting-IP` only when Caddy saw the request arrive
from an address in `CLOUDFLARE_IP_RANGES` (`backend/src/rebate/core/clientip.py`). A stale list does not open a
hole: new Cloudflare ranges are simply not recognised, so their traffic is attributed to the Cloudflare edge address
(rate limits then pool by edge, and audit shows the edge IP). So update when Cloudflare announces changes, and at
least quarterly.

Status: **written, not executed**. The application never fetches the list; there is no runtime network call.

## Source

Cloudflare publishes the ranges at `https://www.cloudflare.com/ips-v4` and `https://www.cloudflare.com/ips-v6`
(plain text, one CIDR per line; API form: `https://api.cloudflare.com/client/v4/ips`). Change notices are announced
in Cloudflare's changelog.

## Update procedure (operator, with internet access)

1. Download both lists and review the diff against the current value (`grep CLOUDFLARE_IP_RANGES .env.prod`).
2. Join them with commas, no spaces:
   `{ curl -fsS https://www.cloudflare.com/ips-v4; echo; curl -fsS https://www.cloudflare.com/ips-v6; } | grep -v '^$' | paste -sd, -`
3. Put the result in `CLOUDFLARE_IP_RANGES` in `.env.prod`. A malformed entry makes the API refuse to start
   (validated at startup), which is the intended fail-loud behaviour.
4. Apply the same list to the origin firewall (allow 80/443 only from these ranges at the provider firewall or in the `DOCKER-USER` iptables chain; ufw/nftables INPUT rules do not filter Docker-published ports).
5. `docker compose --env-file .env.prod -f infra/docker/compose.prod.yml up -d api worker`; check
   `/api/v1/health/ready`.
6. Verify: make a request through Cloudflare, then confirm in the `audit_log` (or a failed-login row) that `ip` is
   your own address and not a Cloudflare edge address.
7. Record the date in the ops log.

## Origin lock (operator step, defence in depth)

Spoofing is already prevented in the application (an untrusted peer or a non-Cloudflare hop never gets
`CF-Connecting-IP` honoured). The firewall allow-list above, and optionally Authenticated Origin Pulls (Cloudflare
presents a client certificate; configure Caddy `tls { client_auth { mode require_and_verify ... } }` with
Cloudflare's origin-pull CA), keep non-Cloudflare traffic away from the origin entirely.
