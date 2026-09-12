"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion without rewrite."""
from __future__ import annotations

import sys

__version__ = "1.0.0"
__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
]

# APP data layer
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session() -> Session:
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        from app.db import get_session as _gs
        from app.models import (
            McpServerRegistry as _MSR,
            McpLlmAxisScore as _MLAS,
            McpScoreDispute as _MSD,
        )
        assert get_session is _gs
        assert McpServerRegistry is _MSR
        assert McpLlmAxisScore is _MLAS
        assert McpScoreDispute is _MSD
        print("PASS")
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)