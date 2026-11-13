"""Client IP for rate limits and audit (S1 review finding 18; Cloudflare handling S11).

``X-Forwarded-For`` is honoured only when the direct peer is a trusted proxy (Caddy). The client is the
rightmost address that is not itself a trusted proxy, so a client cannot spoof its IP by sending its
own ``X-Forwarded-For`` (the left part of the header is client-controlled).

Behind Cloudflare, Caddy overwrites ``X-Forwarded-For`` with the TCP peer it saw. When that address
(the rightmost untrusted hop) lies in a configured Cloudflare range, the request provably arrived via
Cloudflare and ``CF-Connecting-IP`` (set by Cloudflare, which replaces any client-sent value) is the
client. In every other case the header is ignored. Ranges: ``CLOUDFLARE_IP_RANGES``, runbook
``infra/runbooks/cloudflare-ip-update.md``; nothing is fetched at runtime.
"""

from collections.abc import Sequence
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network


def parse_networks(ranges: Sequence[str]) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(r, strict=False) for r in ranges)


def client_ip(
    peer: str | None,
    forwarded_for: str | None,
    trusted: tuple[str, ...],
    cf_connecting_ip: str | None = None,
    cloudflare_ranges: Sequence[str] = (),
) -> str | None:
    if peer is None:
        return None
    if peer not in trusted or not forwarded_for:
        return _valid(peer)
    for hop in reversed([h.strip() for h in forwarded_for.split(",")]):
        if hop and hop not in trusted:
            if cf_connecting_ip and _in(hop, cloudflare_ranges):
                cf = _valid(cf_connecting_ip.strip())
                if cf:
                    return cf  # malformed value: fall through to the X-Forwarded-For result
            # an unparsable hop must not collapse every such client into one shared "None" bucket
            return _valid(hop) or _valid(peer)
    return _valid(peer)


def _in(addr: str, ranges: Sequence[str]) -> bool:
    try:
        ip = ip_address(addr)
    except ValueError:
        return False
    return any(ip.version == n.version and ip in n for n in parse_networks(ranges))


def _valid(value: str) -> str | None:
    try:
        return str(ip_address(value))
    except ValueError:
        return None
