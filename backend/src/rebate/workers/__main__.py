"""Worker process: ``python -m rebate.workers`` (ARCHITECTURE.md §6).

Same safety checks as the API (refuses owner/superuser/BYPASSRLS roles), makes sure the bucket exists,
then claims and runs jobs until SIGTERM/SIGINT; a job in progress is finished before exiting.
"""

import signal
import time

import structlog

from rebate.core.config import get_settings
from rebate.core.db import assert_safe_role, make_engine
from rebate.core.jobs import run_once
from rebate.core.logging import configure_logging
from rebate.modules import (  # noqa: F401  (each registers its job handlers)
    agreements,
    claims,
    extraction,
    files,
    identity,
    ingestion,
    reconciliation,
    recovery,
    reporting,
    tenancy,
)
from rebate.modules.files import storage

log = structlog.get_logger("rebate.worker")
IDLE_SLEEP_SECONDS = 1.0


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())
    assert_safe_role(engine)
    if settings.app_env in ("staging", "production"):
        # hosted: the bucket is provisioned out of band (s3_tar.py init); fail loudly if it is not usable
        try:
            storage.client(settings).head_bucket(Bucket=settings.s3_bucket)
        except Exception as exc:
            raise SystemExit(f"S3 bucket '{settings.s3_bucket}' is missing or inaccessible: {exc}") from exc
    else:
        storage.ensure_bucket(settings)
    stop = False

    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log.info("worker.started")
    while not stop:
        try:
            if not run_once(engine, settings):
                time.sleep(IDLE_SLEEP_SECONDS)
        except Exception as exc:  # database briefly unavailable: keep the process alive
            log.error("worker.loop_error", error=type(exc).__name__)
            time.sleep(5)
    engine.dispose()
    log.info("worker.stopped")


if __name__ == "__main__":
    main()
