"""SQLAlchemy engine + session dependency. The single data-access seam every
feature router depends on via `Depends(get_session)` -- the same contract the
factory modules already target. Postgres in deploy, sqlite in dev/CI.
"""
from __future__ import annotations
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from .settings import settings


def _normalize_url(url: str) -> str:
    # Heroku/legacy DSNs come as postgres://; SQLAlchemy 2.x wants an explicit
    # driver. Normalise to psycopg (v3, already a CI/runtime dep).
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize_url(settings.DATABASE_URL)
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_session():
    """FastAPI dependency: one Session per request, always closed."""
    s: Session = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def init_db() -> None:
    """Create tables for dev/CI (Alembic owns schema in real deploys)."""
    from . import models  # noqa: F401  (register mappers)
    Base.metadata.create_all(bind=engine)
