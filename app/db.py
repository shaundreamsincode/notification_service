"""Engine and session plumbing. Synchronous SQLAlchemy per design D9."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session."""
    with sessionmaker(get_engine(), expire_on_commit=False)() as session:
        yield session
