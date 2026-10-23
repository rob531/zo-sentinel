# services/staged/scoring_gap_api/contract.py
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports
from app.db import get_session
from app.models import (
    Base,
    McpLlmAxisScore,
    McpServerRegistry,
)

router = APIRouter(prefix="/api")


class UnscoredServer(BaseModel):
    server_id: str
    name: str
    first_seen: datetime
    registry_source: str


class ScoringGapResponse(BaseModel):
    total_servers: int
    scored_count: int
    unscored_count: int
    unscored_sample: List[UnscoredServer]


@router.get(
    "/scoring/gap",
    response_model=ScoringGapResponse,
    summary="Return scoring gap statistics",
)
def get_scoring_gap(session: Session = Depends(get_session)):
    # total servers
    total_stmt = select(func.count()).select_from(McpServerRegistry)
    total_servers = session.execute(total_stmt).scalar_one()

    # servers that have at least one axis score
    scored_subq = (
        select(McpServerRegistry.server_id)
        .join(McpLlmAxisScore, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .distinct()
    )
    scored_stmt = select(func.count()).select_from(scored_subq.subquery())
    scored_count = session.execute(scored_stmt).scalar_one()

    unscored_count = total_servers - scored_count

    # sample of unscored servers (limit 10)
    unscored_stmt = (
        select(McpServerRegistry)
        .outerjoin(
            McpLlmAxisScore, McpLlmAxisScore.server_id == McpServerRegistry.server_id
        )
        .where(McpLlmAxisScore.id.is_(None))
        .limit(10)
    )
    unscored_rows = session.execute(unscored_stmt).scalars().all()
    unscored_sample = [
        UnscoredServer(
            server_id=row.server_id,
            name=row.name,
            first_seen=row.first_seen,
            registry_source=row.registry_source,
        )
        for row in unscored_rows
    ]

    return ScoringGapResponse(
        total_servers=total_servers,
        scored_count=scored_count,
        unscored_count=unscored_count,
        unscored_sample=unscored_sample,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # Build a tiny FastAPI app for the test
    test_app = FastAPI()
    test_app.include_router(router)

    # In‑memory SQLite engine (StaticPool for thread‑safety)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with TestSession() as db:
        # three servers
        srv1 = McpServerRegistry(
            server_id="srv-1",
            name="Server One",
            first_seen=datetime.utcnow(),
            registry_source="source-a",
            confidence=0.0,
            description="",
            last_assessed=None,
            last_scanned=None,
            last_seen=None,
            meta={},
            risk_tier="",
            scan_count=0,
            trust_score=0.0,
            url="",
            verdict="",
            verdict_reasoning="",
        )
        srv2 = McpServerRegistry(
            server_id="srv-2",
            name="Server Two",
            first_seen=datetime.utcnow(),
            registry_source="source-b",
            confidence=0.0,
            description="",
            last_assessed=None,
            last_scanned=None,
            last_seen=None,
            meta={},
            risk_tier="",
            scan_count=0,
            trust_score=0.0,
            url="",
            verdict="",
            verdict_reasoning="",
        )
        srv3 = McpServerRegistry(
            server_id="srv-3",
            name="Server Three",
            first_seen=datetime.utcnow(),
            registry_source="source-c",
            confidence=0.0,
            description="",
            last_assessed=None,
            last_scanned=None,
            last_seen=None,
            meta={},
            risk_tier="",
            scan_count=0,
            trust_score=0.0,
            url="",
            verdict="",
            verdict_reasoning="",
        )
        db.add_all([srv1, srv2, srv3])
        db.flush()

        # one axis score for srv-1
        score = McpLlmAxisScore(
            id=1,
            server_id="srv-1",
            adapter_sha256="",
            axis_name="test_axis",
            decision_rule_version="",
            escalated=False,
            escalated_to=None,
            label="",
            label_index=0,
            model_version="",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.0,
            probs={},
            scored_at=datetime.utcnow(),
        )
        db.add(score)
        db.commit()

    # Run the test client
    client = TestClient(test_app)
    resp = client.get("/api/scoring/gap")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data.get("scored_count") != 1 or data.get("unscored_count") != 2:
        print(
            f"FAIL: expected scored=1 unscored=2 got scored={data.get('scored_count')} unscored={data.get('unscored_count')}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("PASS")
    sys.exit(0)