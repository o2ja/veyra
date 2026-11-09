"""request_context: one transaction per request, a new one for the next, no tenant context without a
session, commit before the response, rollback on error, commit of failure bookkeeping."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from rebate.api.deps import RequestContextDep
from rebate.core.access import public
from rebate.core.errors import DomainError
from rebate.core.request import ctx
from tests.conftest import DbUrls
from tests.support import api_client, make_app


class _Boom(DomainError):
    code = "conflict"
    status_code = 409


class _BoomCommit(_Boom):
    commit = True


def _app(url: str):  # type: ignore[no-untyped-def]
    app = make_app(url)
    router = APIRouter(dependencies=[Depends(public())])

    @router.get("/probe")
    def probe(request: Request) -> dict[str, str | None]:
        db = ctx(request).db
        first = db.execute(text("SELECT txid_current()::text")).scalar_one()
        second = db.execute(text("SELECT txid_current()::text")).scalar_one()
        assert first == second
        tenant = db.execute(text("SELECT NULLIF(current_setting('app.tenant_id', true), '')")).scalar_one()
        return {"txid": first, "tenant": tenant}

    @router.post("/write")
    def write(request: Request, mode: str) -> dict[str, str]:
        key = f"probe:{mode}:{request.state.request_id}"
        ctx(request).db.execute(
            text("INSERT INTO rate_limit_counters VALUES (:k, now(), 1, now() + interval '1 hour')"),
            {"k": key},
        )
        if mode == "fail":
            raise _Boom()
        if mode == "fail_commit":
            raise _BoomCommit()
        return {"key": key}

    app.include_router(router, prefix="/api/v1", dependencies=[RequestContextDep])
    return app


def _exists(db_url: str, key_prefix: str) -> int:
    from sqlalchemy import create_engine

    engine = create_engine(db_url)
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM rate_limit_counters WHERE key LIKE :p"), {"p": f"{key_prefix}%"}
        ).scalar_one()
    engine.dispose()
    return int(n)


def test_one_transaction_per_request_without_tenant(migrated_db: DbUrls) -> None:
    with api_client(_app(migrated_db.app)) as api:
        a = api.get("/probe").json()
        b = api.get("/probe").json()
    assert a["tenant"] is None and b["tenant"] is None
    assert a["txid"] != b["txid"]


def test_commit_rollback_and_failure_bookkeeping(migrated_db: DbUrls) -> None:
    with api_client(_app(migrated_db.app)) as api:
        ok = api.client.post("/api/v1/write?mode=ok", headers=api._headers(None))
        fail = api.client.post("/api/v1/write?mode=fail", headers=api._headers(None))
        kept = api.client.post("/api/v1/write?mode=fail_commit", headers=api._headers(None))
    assert ok.status_code == 200 and fail.status_code == 409 and kept.status_code == 409
    assert _exists(migrated_db.admin, f"probe:ok:{ok.headers['X-Request-Id']}") == 1
    assert _exists(migrated_db.admin, f"probe:fail:{fail.headers['X-Request-Id']}") == 0  # rolled back
    assert _exists(migrated_db.admin, f"probe:fail_commit:{kept.headers['X-Request-Id']}") == 1  # committed
