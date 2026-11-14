"""The audit_seals migration starts a seal job for tenants that already exist (previous revision -> head)."""

import os

from alembic import command
from sqlalchemy import create_engine, make_url, text

from rebate.core.ids import new_id
from tests.conftest import DbUrls, alembic_config

PREVIOUS = "20261114_0900_calc_runs"
SCRATCH = "rebate_test_k1_seal_mig"


def test_existing_tenants_get_exactly_one_seal_job(migrated_db: DbUrls) -> None:
    base = make_url(os.environ["TEST_ADMIN_DATABASE_URL"])
    server = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH} OWNER rebate_owner"))
    owner = make_url(migrated_db.owner).set(database=SCRATCH).render_as_string(hide_password=False)
    admin = create_engine(base.set(database=SCRATCH))
    try:
        cfg = alembic_config(owner)
        command.upgrade(cfg, PREVIOUS)
        tid = new_id()
        with admin.begin() as conn:
            conn.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'Old (SYNTHETIC)', :s)"),
                {"i": tid, "s": f"old-{tid.hex[-12:]}"},
            )
        command.upgrade(cfg, "head")
        with admin.connect() as conn:
            kinds = conn.execute(text("SELECT kind, status FROM jobs WHERE tenant_id = :t"), {"t": tid}).all()
            forced = conn.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'tenants'")
            ).scalar_one()
        assert [tuple(k) for k in kinds] == [("audit_seal", "queued")]
        assert forced is True  # FORCE is back on after the data step
    finally:
        admin.dispose()
        with server.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        server.dispose()
