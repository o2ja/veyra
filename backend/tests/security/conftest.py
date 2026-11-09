"""Security tests: real API app on the migrated test DB, advancing fake clock, Mailpit for email."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from tests.conftest import DbUrls
from tests.support import Api, FakeClock, api_client, fake_clock, make_app


@pytest.fixture
def clk() -> Iterator[FakeClock]:
    with fake_clock() as fc:
        yield fc


@pytest.fixture
def app(migrated_db: DbUrls) -> FastAPI:
    return make_app(migrated_db.app)


@pytest.fixture
def api(app: FastAPI, clk: FakeClock) -> Iterator[Api]:
    with api_client(app) as client:
        yield client


@pytest.fixture
def api2(app: FastAPI, clk: FakeClock) -> Iterator[Api]:
    """A second browser (separate cookie jar)."""
    with api_client(app) as client:
        yield client
