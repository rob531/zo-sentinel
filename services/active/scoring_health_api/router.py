# deps: fastapi, sqlalchemy, pydantic, requests
"""Scoring Health API

Provides a simple health endpoint exposing basic statistics about the scoring
service. The endpoint is public (no authentication) and demonstrates proper
use of the shared DB session dependency.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Import the shared DB session dependency and ORM models.
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_health_api"])


class HealthResponse(BaseModel):
    """Response model for the health endpoint."""

    server_count: int = Field(..., description="Number of registered servers")
    score_count: int = Field(..., description="Number of LLM axis score rows")
    status: str = Field(..., description="Overall health status")


@router.get("/scoring_health", response_model=HealthResponse)
def get_scoring_health(db: Session = Depends(get_session)):
    """Return basic health metrics for the scoring subsystem.

    The function queries the two core tables and returns their row counts. Any
    database error results in a 503 Service Unavailable response.
    """
    try:
        server_cnt = db.query(McpServerRegistry).count()
        score_cnt = db.query(McpLlmAxisScore).count()
    except Exception as exc:
        # In production we would log the exception; here we surface a generic error.
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    # Simple health heuristic: if we have any servers and scores we are "ok".
    overall_status = "ok" if server_cnt > 0 and score_cnt > 0 else "degraded"
    return HealthResponse(server_count=server_cnt, score_count=score_cnt, status=overall_status)


# ---------------------------------------------------------------------------
# Self‑test (executed when running the module directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys, pathlib
    # Ensure the repo root is on the import path so `from app.db` resolves.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from contextlib import contextmanager

    # Create an in‑memory SQLite database and initialise the tables.
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    # Import the Base metadata from the models module and create tables.
    try:
        from app.models import Base
        Base.metadata.create_all(engine)
    except Exception as e:
        print("FAIL: unable to create test tables", e)
        sys.exit(1)

    @contextmanager
    def test_session() -> Session:
        """Yield a SQLAlchemy session bound to the in‑memory engine.
        The session is rolled back after use to keep the database clean.
        """
        db = SessionLocal()
        try:
            yield db
        finally:
            db.rollback()
            db.close()

    # Override the get_session dependency with our test session.
    def override_get_session() -> Session:
        with test_session() as sess:
            yield sess

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Happy path
    resp = client.get("/api/scoring_health")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    expected_keys = {"server_count", "score_count", "status"}
    if not expected_keys.issubset(data.keys()):
        print("FAIL: response missing keys", data)
        sys.exit(1)

    # Simulate empty tables (already empty) – status should be 'degraded'
    if data["status"] != "degraded":
        print("FAIL: expected degraded status for empty tables", data)
        sys.exit(1)

    print("PASS")
    sys.exit(0)
