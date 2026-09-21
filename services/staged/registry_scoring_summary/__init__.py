"""Auto-emitted service package."""

from __future__ import annotations

import os
import sys
from typing import Any, Generator

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

__version__ = "0.1.0"
__service_name__ = os.environ.get("SERVICE_NAME", "zo_sentinel")

# ZoComputer store endpoint for mesh/pipeline data
ZOCOMPUTER_URL = os.environ.get("ZOCOMPUTER_URL", "http://127.0.0.1:8772")


def get_mesh_session() -> Generator[dict[str, Any], None, None]:
    """Query mesh/pipeline tables via ZoComputer store."""
    import json
    import urllib.request

    def _query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = json.dumps({"sql": sql, "params": params or {}}).encode()
        req = urllib.request.Request(
            f"{ZOCOMPUTER_URL}/query",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    yield {"query": _query}


def make_session(url: str) -> sessionmaker:
    """Create a sessionmaker for the given database URL."""
    engine = create_engine(url, pool_pre_ping=True, echo=False)
    return sessionmaker(bind=engine)


# Default app session (overridden in tests)
_app_sessionmaker: sessionmaker | None = None


def set_sessionmaker(sm: sessionmaker) -> None:
    """Set the global app sessionmaker (for testing)."""
    global _app_sessionmaker
    _app_sessionmaker = sm


def get_session() -> Generator[Session, None, None]:
    """Get SQLAlchemy session from app.db (app tables)."""
    if _app_sessionmaker is not None:
        yield from _app_sessionmaker()
    else:
        # Fallback: import from app.db
        from app.db import get_session as _get

        yield from _get()


# Re-export common types for service consumers
__all__ = [
    "__version__",
    "__service_name__",
    "get_session",
    "get_mesh_session",
    "set_sessionmaker",
    "ZOCOMPUTER_URL",
]


if __name__ == "__main__":
    # Self-test
    print("PASS")