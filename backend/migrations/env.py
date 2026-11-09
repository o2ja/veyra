"""Alembic environment. Runs as the owner role (DATABASE_URL_OWNER); online mode only."""

import os

from alembic import context
from sqlalchemy import create_engine

from rebate.core.db import Base

config = context.config


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL_OWNER"]
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported; run against a database")
run_migrations_online()
