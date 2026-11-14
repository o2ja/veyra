import pytest
from sqlalchemy import Engine, text


@pytest.fixture(autouse=True)
def _fresh_job_throttles(admin_engine: Engine) -> None:
    """The fake clock never advances on its own, so the per-user job-request counters (R3) would carry over."""
    with admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM rate_limit_counters WHERE key LIKE 'jobs:%'"))
