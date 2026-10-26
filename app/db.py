"""Engine and session plumbing. Synchronous SQLAlchemy per design D9."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session on the app's engine."""
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session
