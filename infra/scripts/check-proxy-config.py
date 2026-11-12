"""Fail if TRUSTED_PROXY_IPS does not name Caddy's fixed address on a network the api shares with it.

The api sees the TCP peer on the network it shares with caddy; trusting any other address (e.g. caddy's
edge-network address) makes every user pool under one IP. Usage: check-proxy-config.py ENV_FILE COMPOSE_FILE
"""

import ipaddress
import json
import subprocess
import sys


def check(cfg: dict, trusted: set[str]) -> list[str]:
    svc, nets = cfg["services"], cfg["networks"]
    shared = set(svc["api"]["networks"]) & set(svc["caddy"]["networks"])
    expected = set()
    for n in shared:
        addr = (svc["caddy"]["networks"][n] or {}).get("ipv4_address")
        subnets = [c["subnet"] for c in nets[n].get("ipam", {}).get("config", [])]
        if not addr or not any(ipaddress.ip_address(addr) in ipaddress.ip_network(s) for s in subnets):
            return [f"caddy has no fixed ipv4_address inside a declared subnet on shared network '{n}'"]
        expected.add(addr)
    if not expected:
        return ["api and caddy share no network"]
    return [] if trusted == expected else [f"TRUSTED_PROXY_IPS={sorted(trusted)} but api sees caddy as {sorted(expected)}"]


if __name__ == "__main__":
    env, compose = sys.argv[1:3]
    out = subprocess.run(
        ["docker", "compose", "--env-file", env, "-f", compose, "config", "--format", "json"],
        check=True, capture_output=True, text=True,
    ).stdout
    cfg = json.loads(out)
    trusted = {x.strip() for x in cfg["services"]["api"]["environment"]["TRUSTED_PROXY_IPS"].split(",") if x.strip()}
    errors = check(cfg, trusted)
    print("\n".join(errors) or "trusted proxy config matches the compose network")
    sys.exit(1 if errors else 0)
