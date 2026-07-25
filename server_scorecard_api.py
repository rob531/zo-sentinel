from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel
from typing import List

from app.db import get_session, Base
from app.models import McpLlmAxisScores

router = APIRouter()


class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class ScorecardResponse(BaseModel):
    server_id: int
    verdict: str
    risk_tier: str
    axes: List[AxisScore]
    scored_at: str
    model_version: str


@router.get(
    "/servers/{server_id}/scorecard",
    response_model=ScorecardResponse,
    tags=["scorecard"],
)
def get_scorecard(server_id: int, session: Session = Depends(get_session)):
    stmt = select(McpLlmAxisScores).where(McpLlmAxisScores.server_id == server_id)
    rows = session.execute(stmt).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Scorecard not found")

    axes: List[AxisScore] = []
    escalated_any = False
    for r in rows:
        axes.append(
            AxisScore(
                axis_name=r.axis_name,
                label=r.label,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                escalated=r.escalated,
            )
        )
        if r.escalated:
            escalated_any = True

    risk_tier = "elevated" if escalated_any else "standard"
    verdict = risk_tier

    scored_at = rows[0].scored_at.isoformat()
    model_version = rows[0].model_version

    return ScorecardResponse(
        server_id=server_id,
        verdict=verdict,
        risk_tier=risk_tier,
        axes=axes,
        scored_at=scored_at,
        model_version=model_version,
    )


if __name__ == "__main__":
    import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create test FastAPI app
    app = FastAPI()
    app.include_router(router)

    # In‑memory SQLite for self‑test
    TEST_DB_URL = "sqlite:///:memory:"
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed test data
    def seed():
        session = TestingSessionLocal()
        axes = [
            "overall_risk",
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        ]
        rows = []
        now = datetime.datetime.utcnow()
        for i, axis in enumerate(axes):
            rows.append(
                McpLlmAxisScores(
                    server_id=1,
                    axis_name=axis,
                    label=f"Label {axis}",
                    p_top=0.1 * i,
                    p_critical=0.2 * i,
                    p_danger=0.3 * i,
                    escalated=(axis == "overall_risk"),
                    model_version="v1.0",
                    adapter_sha256="dummysha",
                    scored_at=now,
                )
            )
        session.add_all(rows)
        session.commit()
        session.close()

    seed()

    # Override dependency
    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Run self‑test
    client = TestClient(app)
    resp = client.get("/servers/1/scorecard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["server_id"] == 1
    assert len(data["axes"]) == 7
    escalated_axes = [a for a in data["axes"] if a["escalated"]]
    assert len(escalated_axes) == 1
    print("PASS")