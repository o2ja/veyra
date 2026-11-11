"""Alembic environment. Runs as the owner role (DATABASE_URL_OWNER); online mode only."""

import os

from alembic import context
from sqlalchemy import create_engine

from rebate.core import jobs as _jobs_model  # noqa: F401  (register tables)
from rebate.core.db import Base
from rebate.modules.agreements import models as _agreements_models  # noqa: F401
from rebate.modules.audit import models as _audit_models  # noqa: F401
from rebate.modules.claims import models as _claims_models  # noqa: F401
from rebate.modules.extraction import models as _extraction_models  # noqa: F401
from rebate.modules.files import models as _files_models  # noqa: F401
from rebate.modules.identity import models as _identity_models  # noqa: F401
from rebate.modules.ingestion import models as _ingestion_models  # noqa: F401
from rebate.modules.masterdata import models as _masterdata_models  # noqa: F401
from rebate.modules.tenancy import models as _tenancy_models  # noqa: F401

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
