from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


def _tier_column():
    for col in McpServerRegistry.__table__.c:
        if "tier" in col.name:
            return col
    raise RuntimeError("Tier column not found")


class TierTrend(BaseModel):
    tier: str
    count: int
    trend: int


class OverviewResponse(BaseModel):
    overview: Dict[str, int]
    trends: List[TierTrend]


@router.get("/risk/overview", response_model=OverviewResponse)
def get_overview(days: int = 7, session: Session = Depends(get_session)):
    tier_col = _tier_column()
    counts = (
        session.query(tier_col, func.count())
        .group_by(tier_col)
        .all()
    )
    overview = {str(tier): cnt for tier, cnt in counts}
    trends: List[Dict[str, Any]] = []  # placeholder for future trend logic
    return {"overview": overview, "trends": trends}


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Session:
        return TestSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # seed data
    tier_col = _tier_column()
    id_col = None
    for col in McpServerRegistry.__table__.c:
        if col.name.endswith("id"):
            id_col = col.name
            break
    if id_col is None:
        raise RuntimeError("ID column not found")

    servers = [
        {id_col: 1, "server_name": "srv1", tier_col.name: "high"},
        {id_col: 2, "server_name": "srv2", tier_col.name: "medium"},
        {id_col: 3, "server_name": "srv3", tier_col.name: "low"},
    ]

    with TestSession() as s:
        s.execute(McpServerRegistry.__table__.insert(), servers)
        s.commit()

    client = TestClient(app)
    resp = client.get("/api/risk/overview?days=2")
    assert resp.status_code == 200
    data = resp.json()
    assert "overview" in data
    assert data["overview"].get("high") == 1
    print("PASS")
    sys.exit(0)