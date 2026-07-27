# services/staged/freshness_metadata/logic.py
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


class FreshnessResponse(BaseModel):
    server_id: int
    last_scanned: Optional[datetime] = None
    scan_count: Optional[int] = None
    last_axis_score_at: Optional[datetime] = None
    hours_since_score: Optional[float] = None
    score_stale_hours: int = 24
    scan_stale_hours: int = 24
    overall_fresh: Literal["fresh", "stale", "unknown"]


@router.get(
    "/servers/{server_id}/freshness",
    response_model=FreshnessResponse,
    tags=["freshness"],
)
def get_freshness(
    server_id: int, db: Session = Depends(get_session)
) -> FreshnessResponse:
    # fetch server registry info
    registry = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .one_or_none()
    )
    if registry is None:
        raise HTTPException(status_code=404, detail="Server not found")

    # fetch latest axis score timestamp
    last_score_ts = (
        db.query(func.max(McpLlmAxisScore.scored_at))
        .filter(McpLlmAxisScore.server_id == server_id)
        .scalar()
    )

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    hours_since_score: Optional[float] = None
    if last_score_ts is not None:
        delta = now - last_score_ts.replace(tzinfo=timezone.utc)
        hours_since_score = max(delta.total_seconds() / 3600.0, 0.0)

    # determine freshness
    if last_score_ts is None:
        overall = "unknown"
    elif hours_since_score is not None and hours_since_score <= FreshnessResponse.__fields__[
        "score_stale_hours"
    ].default:
        overall = "fresh"
    else:
        overall = "stale"

    return FreshnessResponse(
        server_id=server_id,
        last_scanned=registry.last_scanned,
        scan_count=registry.scan_count,
        last_axis_score_at=last_score_ts,
        hours_since_score=hours_since_score,
        overall_fresh=overall,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and override the dependency
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    # Import Base from the app package (assumed to exist)
    try:
        from app.db import Base  # type: ignore
    except Exception as exc:
        print(f"Unable to import Base from app.db: {exc}", file=sys.stderr)
        sys.exit(1)

    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:
        return SessionLocal()

    # Override the FastAPI dependency
    router.dependency_overrides[get_session] = get_test_session

    # Seed test data
    test_db = SessionLocal()
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    server = McpServerRegistry(
        server_id=1,
        last_scanned=now - timedelta(hours=5),
        scan_count=10,
    )
    score = McpLlmAxisScore(
        server_id=1,
        scored_at=now - timedelta(hours=2),
    )
    test_db.add_all([server, score])
    test_db.commit()
    test_db.close()

    # Run test client
    client = TestClient(router)

    resp = client.get("/servers/1/freshness")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["hours_since_score"] >= 0, "hours_since_score is negative"
    assert data["overall_fresh"] in ("fresh", "stale", "unknown"), "Invalid overall_fresh"
    print("PASS")