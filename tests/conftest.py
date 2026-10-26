"""Shared fixtures.

Database tests need a real PostgreSQL database (row locks, partial indexes and
ON CONFLICT cannot be faked). Point ``TEST_DATABASE_URL`` at a database you do
not mind losing: the session fixture drops and recreates its ``public`` schema.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text


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
def db(db_engine: Engine) -> Iterator[Connection]:
    """A connection to the test database; all rows are removed after the test."""
    conn = db_engine.connect()
    try:
        yield conn
    finally:
        conn.close()
        truncate_all(db_engine)
