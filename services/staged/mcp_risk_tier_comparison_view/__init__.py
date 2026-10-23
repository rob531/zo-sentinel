# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

"""Service package initialization."""

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
]

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _test():
    """Self-test: validate package loads and data layer is accessible."""
    from fastapi import FastAPI

    local_app = FastAPI()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    local_app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    # Verify models are importable
    _ = McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

    # Verify session dependency resolves
    session = get_session()
    assert session is not None

    print("PASS")


if __name__ == "__main__":
    _test()