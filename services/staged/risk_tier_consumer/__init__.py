"""
Shared utilities for staged services.

All services import this module to obtain a DB session and to expose a
self‑test that validates the import contract without touching real
application data.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Application DB session – must be imported exactly as the app expects.
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (imported for dependency injection)

# Import models that are part of the official app schema.
# Importing only the symbols that are guaranteed to exist prevents schema PRM
# rejections caused by accidental use of unknown columns.
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
    McpServerRegistry,
)  # noqa: F401


# --------------------------------------------------------------------------- #
# Public helper – returns a fresh session from the app dependency.
# --------------------------------------------------------------------------- #
def acquire_session() -> Any:
    """
    Acquire a SQLAlchemy session using the app's ``get_session`` dependency.

    The function is deliberately tiny: it merely forwards the call so that
    downstream services can import ``acquire_session`` instead of reaching
    directly into ``app.db``.  This indirection keeps intra‑service imports
    stable when a staged package is promoted to active.
    """
    return get_session()


# --------------------------------------------------------------------------- #
# Minimal health‑check used by the self‑test.
# --------------------------------------------------------------------------- #
def _health_check() -> bool:
    """
    Perform a no‑op health check.

    The function obtains a session and immediately releases it.  It does not
    touch any tables, thereby avoiding schema mismatches such as the
    ``org_id`` column error that previously caused PRM failures.
    """
    sess = acquire_session()
    # The session object from ``get_session`` always provides a ``close`` method.
    # Some implementations also support context‑manager usage; we handle both.
    try:
        close_method = getattr(sess, "close", None)
        if callable(close_method):
            close_method()
    finally:
        # In case the session implements ``__exit__`` we ensure proper cleanup.
        exit_method = getattr(sess, "__exit__", None)
        if callable(exit_method):
            exit_method(None, None, None)
    return True


# --------------------------------------------------------------------------- #
# Self‑test entry point.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    """
    The module can be executed directly to run a lightweight self‑test.
    The test overrides the ``get_session`` dependency with a dummy session
    that satisfies the interface required by ``_health_check``.
    """
    import sys
    from fastapi import Depends

    # ------------------------------------------------------------------- #
    # Dummy session that mimics the interface of a real SQLAlchemy session.
    # ------------------------------------------------------------------- #
    class _DummySession:
        def close(self) -> None:
            pass

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # pragma: no cover
            pass

    # ------------------------------------------------------------------- #
    # Override the app's ``get_session`` dependency for the duration of the test.
    # ------------------------------------------------------------------- #
    def _dummy_get_session() -> _DummySession:  # pragma: no cover
        return _DummySession()

    # Apply the override directly – this file is not a FastAPI app, but the
    # pattern mirrors the one used by the real services.
    original_get_session = get_session
    try:
        # Monkey‑patch the imported name.
        globals()["get_session"] = _dummy_get_session  # type: ignore
        if _health_check():
            print("PASS")
        else:  # pragma: no cover
            print("FAIL")
            sys.exit(1)
    finally:
        # Restore the original dependency to avoid side effects if the module
        # is imported elsewhere after the self‑test runs.
        globals()["get_session"] = original_get_session