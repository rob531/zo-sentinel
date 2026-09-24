"""
Auto-emitted service package.
Relative intra-service imports survive staged->active promotion without rewrite.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
    VulnAdvisory,
)

__version__ = "1.0.0"

__all__ = [
    "McpLlmAxisScore",
    "McpScoreDispute",
    "McpServerRegistry",
    "Org",
    "User",
    "VulnAdvisory",
    "get_session",
    "get_db",
    "DatabaseSession",
    "AsyncDatabaseSession",
]


async def get_db() -> AsyncGenerator[Session, None]:
    """Dependency for async database sessions."""
    session = Session(bind=get_session().bind)
    try:
        yield session
    finally:
        session.close()


def DatabaseSession(
    session: Annotated[Session, Depends(get_session)],
) -> Session:
    """Shorthand dependency for sync database sessions."""
    return session


async def AsyncDatabaseSession(
    session: Annotated[Session, Depends(get_db)],
) -> Session:
    """Shorthand dependency for async database sessions."""
    return session


def _build_self_test_db():
    """Create in-memory SQLite engine for self-test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create all tables
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, TestingSessionLocal


def run_self_test() -> bool:
    """Run self-test validation. Returns True if PASS."""
    engine, TestingSessionLocal = _build_self_test_db()

    test_app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Verify dependency override is set
    if get_session not in test_app.dependency_overrides:
        print("FAIL: dependency override not set")
        return False

    # Verify models are importable
    required_models = [
        McpServerRegistry,
        McpLlmAxisScore,
        McpScoreDispute,
        Org,
        User,
        VulnAdvisory,
    ]
    for model in required_models:
        if model is None:
            print(f"FAIL: {model.__name__} not available")
            return False

    # Verify session creation works
    with TestingSessionLocal() as session:
        if session is None:
            print("FAIL: could not create session")
            return False
        # Verify we can query
        _ = session.query(Org).first()

    engine.dispose()
    return True


if __name__ == "__main__":
    if run_self_test():
        print("PASS")
        sys.exit(0)
    else:
        sys.exit(1)