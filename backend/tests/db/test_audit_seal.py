"""S11.1 audit sealing: chain, idempotency, tamper and deleted-seal detection, job rescheduling, CLI exit code,
retention dry run. Real PostgreSQL; tampering is done by the superuser with the append-only trigger switched
off, on a throw-away tenant created by each test (the app role cannot do it at all: isolation suite D9)."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from rebate.core.db import transaction
from rebate.core.ids import new_id
from rebate.core.jobs import REGISTRY, JobContext
from rebate.modules.audit import cli, jobs, seal
from tests.conftest import DbUrls
from tests.support import make_settings

NOW = datetime.now(UTC)


@pytest.fixture
def tenant(admin_engine: Engine) -> UUID:
    tid = new_id()
    with admin_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'Seal (SYNTHETIC)', :s)"),
            {"i": tid, "s": f"seal-{tid.hex[-12:]}"},
        )
        conn.execute(text("INSERT INTO tenant_settings (tenant_id) VALUES (:t)"), {"t": tid})
    return tid


def add_entries(engine: Engine, tid: UUID, n: int, age: timedelta = timedelta(hours=1)) -> None:
    with engine.begin() as conn:
        for i in range(n):
            conn.execute(
                text(
                    "INSERT INTO audit_log (tenant_id, occurred_at, actor_type, action, entity_type, entity_id, "
                    "after, ip, metadata) VALUES (:t, :at, 'system', 'probe.event', 'probe', :e, "
                    "CAST(:a AS jsonb), '10.0.0.1', CAST(:m AS jsonb))"
                ),
                {
                    "t": tid,
                    "at": NOW - age + timedelta(seconds=i),
                    "e": new_id(),
                    "a": json.dumps({"n": i, "name": "Zoë", "amount": "1.50"}),
                    "m": '{"b":1,"a":[1,2]}',
                },
            )


def do_seal(app_engine: Engine, tid: UUID) -> bool:
    with transaction(app_engine, tid) as db:
        return seal.seal_tenant(db, tid, NOW)


def verify(app_engine: Engine, tid: UUID) -> seal.Verification:
    with transaction(app_engine, tid) as db:
        return seal.verify_tenant(db, tid)


def tamper(admin_engine: Engine, table: str, sql: str, **params: object) -> None:
    """Committed tampering on the test's own tenant: trigger off, change, trigger on."""
    with admin_engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER trg_{table}_no_mutation"))
        conn.execute(text(sql), params)
        conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER trg_{table}_no_mutation"))


def seals(admin_engine: Engine, tid: UUID) -> list[int]:
    with admin_engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM audit_seals WHERE tenant_id = :t ORDER BY id"), {"t": tid})
        return list(rows.scalars())


class Row:
    def __init__(self, **values: Any) -> None:
        self._mapping = values


def test_canonical_form_is_fixed() -> None:
    """Hand-derived: sorted keys, compact separators, UTC microsecond timestamp, jsonb kept as its text."""
    row = Row(id=7, occurred_at=datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC), after='{"a": 1.50}', ip=None)
    expected = '{"after":"{\\"a\\": 1.50}","id":7,"ip":null,"occurred_at":"2026-01-02T03:04:05.000006Z"}'
    assert seal.canonical(row) == expected.encode()


def test_chain_is_sha256_of_prev_hash_then_entry() -> None:
    row = Row(id=1, occurred_at=NOW)
    first = hashlib.sha256(seal.canonical(row)).hexdigest()
    second = hashlib.sha256(first.encode() + seal.canonical(row)).hexdigest()
    assert seal.chain(None, [row]) == first
    assert seal.chain(None, [row, row]) == second


def test_seals_in_chunks_is_idempotent_and_verifies(
    app_engine: Engine, admin_engine: Engine, tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seal, "CHUNK", 3)
    add_entries(admin_engine, tenant, 8)
    assert do_seal(app_engine, tenant) is False
    assert len(seals(admin_engine, tenant)) == 3  # 3 + 3 + 2
    assert do_seal(app_engine, tenant) is False
    assert len(seals(admin_engine, tenant)) == 3
    v = verify(app_engine, tenant)
    assert (v.ok, v.seals, v.unsealed_entries, v.failure) == (True, 3, 0, None)
    add_entries(admin_engine, tenant, 2)  # resumes from the last seal
    do_seal(app_engine, tenant)
    assert verify(app_engine, tenant).seals == 4


def test_bounded_run_reports_more_due(
    app_engine: Engine, admin_engine: Engine, tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seal, "CHUNK", 2)
    add_entries(admin_engine, tenant, 5)
    with transaction(app_engine, tenant) as db:
        assert seal.seal_tenant(db, tenant, NOW, max_chunks=2) is True
    assert do_seal(app_engine, tenant) is False
    assert verify(app_engine, tenant).seals == 3


def test_recent_entries_wait_for_the_settle_window(
    app_engine: Engine, admin_engine: Engine, tenant: UUID
) -> None:
    add_entries(admin_engine, tenant, 2, age=timedelta(minutes=1))
    do_seal(app_engine, tenant)
    v = verify(app_engine, tenant)
    assert (v.seals, v.unsealed_entries) == (0, 2)


def test_tampered_entry_is_detected(
    app_engine: Engine, admin_engine: Engine, tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seal, "CHUNK", 3)
    add_entries(admin_engine, tenant, 6)
    do_seal(app_engine, tenant)
    _, second = seals(admin_engine, tenant)
    tamper(
        admin_engine,
        "audit_log",
        "UPDATE audit_log SET action = 'forged.event' "
        "WHERE id = (SELECT max(id) FROM audit_log WHERE tenant_id = :t)",
        t=tenant,
    )
    v = verify(app_engine, tenant)
    assert (v.ok, v.failed_seal_id, v.seals) == (False, second, 1)
    assert "altered" in (v.failure or "")


def test_deleted_audit_entry_is_detected(app_engine: Engine, admin_engine: Engine, tenant: UUID) -> None:
    add_entries(admin_engine, tenant, 4)
    do_seal(app_engine, tenant)
    tamper(
        admin_engine,
        "audit_log",
        "DELETE FROM audit_log WHERE id = (SELECT min(id) + 1 FROM audit_log WHERE tenant_id = :t)",
        t=tenant,
    )
    assert verify(app_engine, tenant).ok is False


@pytest.mark.parametrize("which", ["first", "middle"])
def test_deleted_seal_is_detected(
    app_engine: Engine, admin_engine: Engine, tenant: UUID, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    monkeypatch.setattr(seal, "CHUNK", 2)
    add_entries(admin_engine, tenant, 6)
    do_seal(app_engine, tenant)
    ids = seals(admin_engine, tenant)
    tamper(
        admin_engine,
        "audit_seals",
        "DELETE FROM audit_seals WHERE id = :i",
        i=ids[0 if which == "first" else 1],
    )
    v = verify(app_engine, tenant)
    assert v.ok is False and "prev_hash" in (v.failure or "")


def test_cli_exit_codes(
    app_engine: Engine, admin_engine: Engine, tenant: UUID, capsys: pytest.CaptureFixture[str]
) -> None:
    add_entries(admin_engine, tenant, 3)
    do_seal(app_engine, tenant)
    assert cli.main(["verify", "--tenant", str(tenant)], engine=app_engine) == 0
    tamper(
        admin_engine,
        "audit_log",
        "UPDATE audit_log SET entity_type = 'forged' WHERE tenant_id = :t",
        t=tenant,
    )
    capsys.readouterr()
    assert cli.main(["verify", "--tenant", str(tenant)], engine=app_engine) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_job_seals_and_schedules_the_next_run(
    migrated_db: DbUrls, app_engine: Engine, admin_engine: Engine, tenant: UUID
) -> None:
    add_entries(admin_engine, tenant, 3)
    ctx = JobContext(make_settings(migrated_db.app), new_id(), tenant, {}, 1, None)
    with transaction(app_engine, tenant) as db:
        jobs.run(db, ctx)
    assert verify(app_engine, tenant).seals == 1
    with admin_engine.connect() as conn:
        n, when = conn.execute(
            text("SELECT count(*), min(run_at) FROM jobs WHERE tenant_id = :t AND kind = 'audit_seal'"),
            {"t": tenant},
        ).one()
    assert n == 1 and when > datetime.now(UTC) + timedelta(minutes=50)


def test_retention_report_is_a_dry_run(app_engine: Engine, admin_engine: Engine, tenant: UUID) -> None:
    add_entries(admin_engine, tenant, 2, age=timedelta(days=365 * 8))
    add_entries(admin_engine, tenant, 3, age=timedelta(days=30))
    do_seal(app_engine, tenant)
    with transaction(app_engine, tenant) as db:
        report = seal.retention_report(db, tenant, NOW)
    audit = report["tables"]["audit_log"]
    assert (report["retention_years"], report["dry_run"]) == (7, True)
    assert (audit["total"], audit["past_retention"], audit["past_retention_and_sealed"]) == (5, 2, 2)
    with admin_engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM audit_log WHERE tenant_id = :t"), {"t": tenant})
        assert count.scalar_one() == 5


FORGED = (
    "INSERT INTO audit_log (id, tenant_id, occurred_at, actor_type, action, entity_type) OVERRIDING SYSTEM VALUE "
    "VALUES (:i, :t, now() - interval '1 hour', 'system', 'forged.entry', 'probe')"
)


def test_entry_inserted_inside_a_sealed_range_is_detected(
    app_engine: Engine, admin_engine: Engine, tenant: UUID
) -> None:
    """Ids are global, so another tenant's entry sits inside this tenant's range; forge one in its id."""
    other = new_id()
    with admin_engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'Other (SYNTHETIC)', :s)"),
                     {"i": other, "s": f"seal-{other.hex[-12:]}"})  # fmt: skip
    add_entries(admin_engine, tenant, 1)
    add_entries(admin_engine, other, 1)
    add_entries(admin_engine, tenant, 1)
    do_seal(app_engine, tenant)
    assert verify(app_engine, tenant).ok is True
    with admin_engine.begin() as conn:
        mid = conn.execute(text("SELECT id FROM audit_log WHERE tenant_id = :t"), {"t": other}).scalar_one()
        conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_no_mutation"))
        conn.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": mid})
        conn.execute(text(FORGED), {"i": mid, "t": tenant})
        conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_no_mutation"))
    v = verify(app_engine, tenant)
    assert v.ok is False and "altered" in (v.failure or "")


def test_entry_inserted_into_a_gap_between_seals_is_detected(
    app_engine: Engine, admin_engine: Engine, tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seal A covers ids below a reserved hole, seal B ids above it; a forged row in the hole is in no seal."""
    monkeypatch.setattr(seal, "CHUNK", 2)
    add_entries(admin_engine, tenant, 2)
    do_seal(app_engine, tenant)
    with admin_engine.begin() as conn:
        hole = conn.execute(text("SELECT nextval(pg_get_serial_sequence('audit_log', 'id'))")).scalar_one()
    add_entries(admin_engine, tenant, 2)
    do_seal(app_engine, tenant)
    assert verify(app_engine, tenant).ok is True
    with admin_engine.begin() as conn:
        conn.execute(text(FORGED), {"i": hole, "t": tenant})
    v = verify(app_engine, tenant)
    assert v.ok is False and "outside every sealed range" in (v.failure or "")


def test_open_old_transaction_defers_sealing(app_engine: Engine, admin_engine: Engine, tenant: UUID) -> None:
    """A transaction older than the settle cutoff may still commit rows with lower ids: do not seal yet."""
    add_entries(admin_engine, tenant, 2)
    later = datetime.now(UTC) + timedelta(minutes=10)
    with app_engine.connect() as other:
        other.execute(text("SELECT 1"))  # opens a transaction on the app role (visible in pg_stat_activity)
        with transaction(app_engine, tenant) as db:
            assert seal.seal_tenant(db, tenant, later) is False
        assert verify(app_engine, tenant).seals == 0
        other.rollback()
    with transaction(app_engine, tenant) as db:
        seal.seal_tenant(db, tenant, later)
    assert verify(app_engine, tenant).seals == 1


def test_anchor(
    app_engine: Engine, admin_engine: Engine, tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seal, "CHUNK", 2)
    add_entries(admin_engine, tenant, 4)
    do_seal(app_engine, tenant)
    anchor = verify(app_engine, tenant).head_hash
    assert cli.main(["verify", "--tenant", str(tenant), "--anchor", str(anchor)], engine=app_engine) == 0
    assert cli.main(["verify", "--tenant", str(tenant), "--anchor", "0" * 64], engine=app_engine) == 1
    tamper(admin_engine, "audit_seals", "DELETE FROM audit_seals WHERE id = (SELECT max(id) FROM audit_seals WHERE tenant_id = :t)", t=tenant)  # fmt: skip
    assert cli.main(["verify", "--tenant", str(tenant), "--anchor", str(anchor)], engine=app_engine) == 1


def test_dead_lettered_run_schedules_a_new_one(
    migrated_db: DbUrls, app_engine: Engine, admin_engine: Engine, tenant: UUID
) -> None:
    ctx = JobContext(make_settings(migrated_db.app), new_id(), tenant, {}, 5, None)
    assert REGISTRY[jobs.KIND].on_dead is jobs._dead
    with transaction(app_engine, tenant) as db:
        jobs._dead(db, ctx)
    with admin_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM jobs WHERE tenant_id = :t AND kind = 'audit_seal'"), {"t": tenant}
        )
        assert n.scalar_one() == 1
