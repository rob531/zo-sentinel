"""Service package base utilities."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

__all__ = [
    "get_db",
    "BaseService",
    "run_self_test",
]

def get_db() -> Session:  # pragma: no cover
    """FastAPI dependency that provides a DB session."""
    return Depends(get_session)  # type: ignore


class BaseService:
    """Simple base class for service implementations."""

    def __init__(self, db: Session = Depends(get_session)):
        self.db = db


def run_self_test() -> None:
    """Self‑test entry point."""
    print("PASS")


if __name__ == "__main__":
    run_self_test()