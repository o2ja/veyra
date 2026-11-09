"""Client IP for rate limits and audit (S1 review finding 18).

``X-Forwarded-For`` is honoured only when the direct peer is a trusted proxy (Caddy). The client is the
rightmost address that is not itself a trusted proxy, so a client cannot spoof its IP by sending its
own ``X-Forwarded-For`` (the left part of the header is client-controlled).
"""

from ipaddress import ip_address


def client_ip(peer: str | None, forwarded_for: str | None, trusted: tuple[str, ...]) -> str | None:
    if peer is None:
        return None
    if peer not in trusted or not forwarded_for:
        return _valid(peer)
    for hop in reversed([h.strip() for h in forwarded_for.split(",")]):
        if hop and hop not in trusted:
            # an unparsable hop must not collapse every such client into one shared "None" bucket
            return _valid(hop) or _valid(peer)
    return _valid(peer)


def _valid(value: str) -> str | None:
    try:
        return str(ip_address(value))
    except ValueError:
        return None
