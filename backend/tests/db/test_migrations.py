"""Empty -> head ran in the session fixture; here: it is at head, idempotent, drift-free, complete."""

from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from tests.conftest import DbUrls, alembic_config


def test_database_is_at_single_head(migrated_db: DbUrls, owner_engine: Engine) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_db.owner))
    assert len(script.get_heads()) == 1
    with owner_engine.connect() as conn:
        assert MigrationContext.configure(conn).get_current_revision() == script.get_current_head()


def test_upgrade_again_is_noop_and_no_drift(migrated_db: DbUrls) -> None:
    cfg = alembic_config(migrated_db.owner)
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models and migrations disagree


def test_baseline_objects(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        extensions = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
        assert {"btree_gist", "pg_trgm", "citext"} <= extensions
        assert conn.execute(text("SELECT to_regprocedure('fn_forbid_mutation()')")).scalar()
        defaults = conn.execute(
            text(
                "SELECT pg_get_userbyid(defaclrole), defaclobjtype, array_to_string(defaclacl, ',') "
                "FROM pg_default_acl"
            )
        ).all()
    tables_acl = next(acl for role, kind, acl in defaults if role == "rebate_owner" and kind == "r")
    assert "rebate_app=arwd/" in tables_acl
    assert "rebate_readonly=r/" in tables_acl
    assert "rebate_ai_writer" not in tables_acl
