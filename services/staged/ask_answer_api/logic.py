"""
services/staged/ask_answer_api/logic.py

Shared utilities for staged services. Mirrors the exemplar logic module while
using the real application data layer.
"""

import logging
from fastapi import Depends
from sqlalchemy.orm import Session

# Real application session provider
from app.db import get_session

# --------------------------------------------------------------------------- #
# Logger
# --------------------------------------------------------------------------- #
logger = logging.getLogger("services.staged.ask_answer_api")
if not logger.handlers:
    # Ensure at least one handler so that logging does not get lost in tests
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# --------------------------------------------------------------------------- #
# Dependency helpers
# --------------------------------------------------------------------------- #
def get_db(session: Session = Depends(get_session)) -> Session:
    """
    FastAPI dependency that provides a SQLAlchemy Session.

    The real application injects the session via ``app.db.get_session``.
    In tests the ``get_session`` dependency can be overridden with a
    throw‑away SQLite session.
    """
    return session


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    # Self‑test that the module can be imported and its dependency works
    # with an in‑memory SQLite session (mimicking the test override pattern).
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a temporary SQLite engine and session factory
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the ``get_session`` dependency with a simple callable that
    # returns a fresh SQLite session.
    def _override_get_session() -> Session:
        return SessionLocal()

    # Directly invoke ``get_db`` with the overridden session.
    test_session = _override_get_session()
    db = get_db(session=test_session)

    # Verify that we received a Session instance.
    assert isinstance(db, Session), "Dependency did not return a Session"

    # Clean up the temporary session.
    db.close()
    print("PASS")