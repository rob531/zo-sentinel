from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpLlmAxisScore
from sqlalchemy.orm import Session

router = APIRouter()

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisSnapshot(BaseModel):
    # An axis a scoring pass did not emit is None, not a 500.
    overall_risk: Optional[dict] = None
    auth_strength: Optional[dict] = None
    capability_breadth: Optional[dict] = None
    data_sensitivity: Optional[dict] = None
    network_egress: Optional[dict] = None
    maintainer_trust: Optional[dict] = None
    exploit_surface: Optional[dict] = None


class ScoringTimelineResponse(BaseModel):
    snapshots: List[AxisSnapshot]


@router.get("/servers/{server_id}/scoring-timeline", response_model=ScoringTimelineResponse)
async def get_scoring_timeline(
    server_id: str,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_session),
):
    # GR-29 (2026-11-27): DB-direct. This used to POST its SQL to the factory
    # write_service on 127.0.0.1:8772, which is not in the Fly image, so on Fly the
    # route could only answer 500 (Errno 111). Same rows, parameterized ORM.
    q = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if start_date:
        q = q.filter(McpLlmAxisScore.scored_at >= start_date)
    if end_date:
        q = q.filter(McpLlmAxisScore.scored_at <= end_date)
    rows = (q.order_by(McpLlmAxisScore.scored_at.desc())
             .limit(limit).offset(offset).all())

    snapshots: dict = {}
    for r in rows:
        key = r.scored_at.isoformat() if r.scored_at else None
        if r.axis_name in AXES:
            snapshots.setdefault(key, {})[r.axis_name] = {"p_top": r.p_top, "label": r.label}

    return {"snapshots": [AxisSnapshot(**snap) for snap in snapshots.values()]}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng)
    s = TS()
    i = 1
    for day in (1, 2, 3):
        for ax in AXES:
            s.add(McpLlmAxisScore(id=i, server_id="test-server-1", axis_name=ax, label="LOW",
                                  p_top=0.1 * day, model_version=f"v{day}",
                                  scored_at=datetime(2023, 1, day)))
            i += 1
    s.add(McpLlmAxisScore(id=i, server_id="other", axis_name="overall_risk", label="HIGH",
                          model_version="v1", scored_at=datetime(2023, 1, 1)))
    s.commit(); s.close()

    def _override():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    r = c.get("/servers/test-server-1/scoring-timeline?limit=100")
    assert r.status_code == 200, r.text
    snaps = r.json()["snapshots"]
    assert len(snaps) == 3, snaps
    assert all(set(k for k, v in sn.items() if v) == set(AXES) for sn in snaps), snaps
    r = c.get("/servers/nobody/scoring-timeline")
    assert r.status_code == 200 and r.json()["snapshots"] == []
    print("PASS")
