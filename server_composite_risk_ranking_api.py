"""GET /servers/ranked-by-risk -- registry servers ordered lowest-trust first.

GR-29 (2026-11-27): rewritten DB-direct. The previous version, for every row, POSTed
an f-string SQL (server id formatted into the text) to the factory write_service on
127.0.0.1:8772 -- a bus that is not in the Fly image -- and read `server.id`, a
column McpServerRegistry does not have. On Fly the route could only answer 500.
`verdict` was briefly the registry's raw verdict column; that column is ~99% the
legacy string 'unknown' and is not served publicly (tests/test_public_verdict_column_leak.py).
The `verdict` KEY is kept for consumers and populated from risk_tier, the same as
server_scorecard_api.py and verdict_axis_detail_api.py.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class ServerRiskRanking(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    trust_score: Optional[float] = None
    risk_tier: Optional[str] = None
    last_scanned: Optional[str] = None
    verdict: Optional[str] = None


class RiskRankingResponse(BaseModel):
    servers: List[ServerRiskRanking]
    total: int


@router.get("/servers/ranked-by-risk", response_model=RiskRankingResponse)
async def get_servers_ranked_by_risk(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    risk_tier: Optional[str] = None,
    session: Session = Depends(get_session),
):
    query = session.query(McpServerRegistry)
    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)
    total = query.count()
    # NULL trust last, portably (Postgres NULLS LAST is not SQLite syntax).
    servers = (query.order_by(McpServerRegistry.trust_score.is_(None),
                              asc(McpServerRegistry.trust_score),
                              McpServerRegistry.risk_tier)
                    .limit(limit).offset(offset).all())
    return {
        "servers": [
            {
                "server_id": s.server_id,
                "name": s.name,
                "url": s.url,
                "trust_score": s.trust_score,
                "risk_tier": s.risk_tier,
                "last_scanned": s.last_scanned.isoformat() if s.last_scanned else None,
                "verdict": s.risk_tier,
            }
            for s in servers
        ],
        "total": total,
    }


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    s.add_all([
        McpServerRegistry(server_id="a", name="A", url="https://a", trust_score=0.8, risk_tier="LOW",
                          verdict="TRUSTED_RESEARCH", last_scanned=datetime(2023, 1, 1, tzinfo=timezone.utc)),
        McpServerRegistry(server_id="b", name="B", url="https://b", trust_score=0.3, risk_tier="HIGH",
                          verdict="CAUTION_LIMITED"),
        McpServerRegistry(server_id="c", name="C", url="https://c", trust_score=None, risk_tier="HIGH"),
    ])
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
    r = c.get("/servers/ranked-by-risk")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 3 and [x["server_id"] for x in j["servers"]] == ["b", "a", "c"], j
    assert j["servers"][0]["verdict"] == "HIGH", "verdict is derived from risk_tier"
    assert "CAUTION_LIMITED" not in r.text and "TRUSTED_RESEARCH" not in r.text, "raw verdict column leaked"
    r = c.get("/servers/ranked-by-risk?risk_tier=HIGH")
    assert r.json()["total"] == 2
    print("PASS")
