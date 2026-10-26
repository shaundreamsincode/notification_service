"""Shared fixtures.

Database tests need a real PostgreSQL database (row locks, partial indexes and
ON CONFLICT cannot be faked). Point ``TEST_DATABASE_URL`` at a database you do
not mind losing: the session fixture drops and recreates its ``public`` schema.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text

from app.delivery.worker import Worker
from app.main import create_app
from app.settings import REPO_ROOT, Settings
from app.templates.registry import TemplateRegistry


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set; database tests need a disposable PostgreSQL database"
        )
    return url


@pytest.fixture(scope="session")
def db_engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url, pool_size=20, max_overflow=20)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parent.parent / "alembic")
    )
    # ConfigParser treats % as interpolation; URLs may contain escapes.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def schema(db_engine: Engine, alembic_config: Config) -> Engine:
    """The test database migrated to head."""
    command.upgrade(alembic_config, "head")
    return db_engine


def truncate_all(engine: Engine) -> None:
    with engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalars().all()
        if tables:
            names = ", ".join(f'"{t}"' for t in tables)
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(schema: Engine) -> Iterator[Connection]:
    """A connection to the migrated test database; all rows are removed after the test."""
    conn = schema.connect()
    try:
        yield conn
    finally:
        conn.close()
        truncate_all(schema)


@pytest.fixture
def make_client(db: Connection, database_url: str) -> Iterator[Callable[..., TestClient]]:
    """Build API clients (lifespan running) against the test database.

    Depends on ``db`` so rows are truncated after the test, once clients close.
    """
    clients: list[TestClient] = []

    def _make(template_dir: Path | None = None) -> TestClient:
        settings = Settings(
            DATABASE_URL=database_url,  # type: ignore[call-arg]
            template_dir=template_dir or REPO_ROOT / "templates",
        )
        client = TestClient(create_app(settings))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def make_worker(schema: Engine, database_url: str) -> Callable[..., "Worker"]:
    """Build a Worker over the real sample templates and the given provider."""

    def _make(provider, template_dir: Path | None = None, **settings) -> "Worker":
        config = Settings(
            DATABASE_URL=database_url,  # type: ignore[call-arg]
            template_dir=template_dir or REPO_ROOT / "templates",
            **settings,
        )
        registry = TemplateRegistry.load(config.template_dir)
        return Worker(schema, registry, {"email": provider, "sms": provider}, config, "test-worker")

    return _make
