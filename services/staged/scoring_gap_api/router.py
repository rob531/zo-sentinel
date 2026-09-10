# services/staged/scoring_gap_api/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

from .models import ScoringGapResponse, UnscoredServerSample

router = APIRouter(prefix="/api", tags=["scoring"])


@router.get("/scoring/gap", response_model=ScoringGapResponse)
def get_scoring_gap(session: Session = Depends(get_session)):
    """
    Returns scoring gap metrics: count of servers with vs without axis scores.
    """
    from .logic import compute_scoring_gap
    return compute_scoring_gap(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    import sys
    import os

    # Create in-memory SQLite engine for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data: 3 servers, 1 with axis scores + 2 without
    db: Session = TestingSessionLocal()

    # Server 1: HAS axis scores (scored)
    s1 = McpServerRegistry(
        server_id="srv-001",
        name="scored-server-alpha",
        first_seen="2024-01-15T10:00:00Z",
        registry_source="test_source",
        url="https://alpha.example.com",
        description="A scored server",
    )
    db.add(s1)
    db.commit()
    db.refresh(s1)

    ax1 = McpLlmAxisScore(
        server_id=s1.server_id,
        axis_name="security",
        model_version="v1.0",
        decision_rule_version="r1",
        label="safe",
        label_index=0,
        probs=[0.1, 0.9],
        p_critical=0.1,
        p_danger=0.2,
        p_top=0.9,
        escalated=False,
        scored_at="2024-02-01T12:00:00Z",
    )
    db.add(ax1)
    db.commit()

    # Server 2: NO axis scores (unscored)
    s2 = McpServerRegistry(
        server_id="srv-002",
        name="unscored-server-beta",
        first_seen="2024-02-10T08:00:00Z",
        registry_source="test_source",
        url="https://beta.example.com",
        description="An unscored server",
    )
    db.add(s2)
    db.commit()

    # Server 3: NO axis scores (unscored)
    s3 = McpServerRegistry(
        server_id="srv-003",
        name="unscored-server-gamma",
        first_seen="2024-03-05T14:30:00Z",
        registry_source="manual_entry",
        url="https://gamma.example.com",
        description="Another unscored server",
    )
    db.add(s3)
    db.commit()

    db.close()

    # Build FastAPI app and override dependency
    app = FastAPI()
    app.include_router(router)

    from app.db import get_session as app_get_session
    app.dependency_overrides[app_get_session] = override_get_session

    # Run test client
    client = TestClient(app)
    response = client.get("/api/scoring/gap")

    if response.status_code != 200:
        print(f"FAIL: Expected 200, got {response.status_code}")
        print(f"Response: {response.text}")
        sys.exit(1)

    data = response.json()

    unscored_count = data.get("unscored_count")
    scored_count = data.get("scored_count")

    if unscored_count != 2:
        print(f"FAIL: Expected unscored_count==2, got {unscored_count}")
        sys.exit(1)

    if scored_count != 1:
        print(f"FAIL: Expected scored_count==1, got {scored_count}")
        sys.exit(1)

    print("PASS")