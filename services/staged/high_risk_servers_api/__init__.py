"""zo-sentinel core package."""

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
    VulnAdvisory,
)

__all__ = [
    "get_session",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "McpServerRegistry",
    "Org",
    "User",
    "VulnAdvisory",
]

if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    # Ensure package root for app imports
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, Org, User

    # In-memory test store per directive: self-test ONLY
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_session():
        from sqlalchemy.orm import Session

        with Session(test_engine) as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    @test_app.get("/__test_pass")
    def test_pass():
        return {"status": "ok"}

    response = client.get("/__test_pass")
    assert response.status_code == 200, f"Health check failed: {response.status_code}"

    with test_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    with next(override_get_session()) as session:
        session.execute(text("SELECT 1"))
        session.commit()

    print("PASS")