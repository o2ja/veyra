"""Client IP behind Caddy and Cloudflare (S11): CF-Connecting-IP only from Cloudflare via a trusted proxy.
Expected values are the addresses written in the test inputs (documentation ranges), not code output."""

import pytest
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from rebate.api.deps import RequestContextDep
from rebate.core.access import public
from rebate.core.clientip import client_ip
from rebate.core.request import ctx
from tests.conftest import DbUrls
from tests.support import api_client, make_app, make_settings

CADDY = "10.0.0.2"
CF = (
    "198.51.100.0/24",
    "2001:db8:cf::/48",
)  # RFC 5737 / 3849 documentation ranges standing in for Cloudflare
EDGE = "198.51.100.7"  # a "Cloudflare edge" address


@pytest.mark.parametrize(
    ("peer", "xff", "cf_header", "expected"),
    [
        (CADDY, EDGE, "203.0.113.9", "203.0.113.9"),  # via Cloudflare: header used
        (CADDY, EDGE, "2001:db8::1", "2001:db8::1"),  # IPv6 client
        ("2001:db8:cf::5", None, "203.0.113.9", "2001:db8:cf::5"),  # untrusted peer: everything ignored
        ("203.0.113.50", EDGE, "203.0.113.9", "203.0.113.50"),  # untrusted peer spoofing both headers
        (CADDY, "203.0.113.77", "203.0.113.9", "203.0.113.77"),  # direct to Caddy, not Cloudflare: ignored
        (CADDY, f"203.0.113.9, {EDGE}", "6.6.6.6", "6.6.6.6"),  # Cloudflare is the authority for the header
        (CADDY, EDGE, "not-an-ip", EDGE),  # malformed: fall back to X-Forwarded-For logic
        (CADDY, EDGE, "", EDGE),
        (CADDY, EDGE, None, EDGE),
        (CADDY, "2001:db8:cf::99", "203.0.113.9", "203.0.113.9"),  # IPv6 edge in range
    ],
)
def test_client_ip_cloudflare(peer: str, xff: str | None, cf_header: str | None, expected: str) -> None:
    assert client_ip(peer, xff, (CADDY,), cf_header, CF) == expected


def test_no_ranges_configured_ignores_header() -> None:
    assert client_ip(CADDY, EDGE, (CADDY,), "203.0.113.9", ()) == EDGE


def _app(url: str):  # type: ignore[no-untyped-def]
    app = make_app(url, trusted_proxy_ips=CADDY, cloudflare_ip_ranges=",".join(CF))
    router = APIRouter(dependencies=[Depends(public())])

    @router.get("/whoami")
    def whoami(request: Request) -> dict[str, str | None]:
        return {"ip": ctx(request).ip}

    app.include_router(router, prefix="/api/v1", dependencies=[RequestContextDep])
    return app


def test_request_context_uses_cloudflare_header_only_through_trusted_proxy(migrated_db: DbUrls) -> None:
    app = _app(migrated_db.app)
    with api_client(app, ip=CADDY) as api:
        via_cf = api.client.get(
            "/api/v1/whoami", headers={"X-Forwarded-For": EDGE, "CF-Connecting-IP": "203.0.113.9"}
        )
        bad = api.client.get("/api/v1/whoami", headers={"X-Forwarded-For": EDGE, "CF-Connecting-IP": "x"})
        not_cf = api.client.get(
            "/api/v1/whoami", headers={"X-Forwarded-For": "203.0.113.77", "CF-Connecting-IP": "203.0.113.9"}
        )
    with api_client(app, ip="203.0.113.50") as api:  # untrusted peer
        spoof = api.client.get(
            "/api/v1/whoami", headers={"X-Forwarded-For": EDGE, "CF-Connecting-IP": "203.0.113.9"}
        )
    assert via_cf.json()["ip"] == "203.0.113.9"
    assert bad.json()["ip"] == EDGE
    assert not_cf.json()["ip"] == "203.0.113.77"
    assert spoof.json()["ip"] == "203.0.113.50"


def test_config_rejects_bad_cloudflare_range() -> None:
    with pytest.raises(ValidationError):
        make_settings("postgresql+psycopg://x@y/z", cloudflare_ip_ranges="not-a-cidr")
