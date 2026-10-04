# deps: fastapi, sqlalchemy, pydantic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Dict, List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter(prefix="/api", tags=["service_unit_promotion_readiness"])


class ServerReadiness(BaseModel):
    server_id: int
    name: str
    risk_tier: Optional[str]
    trust_score: Optional[float]
    last_scanned: Optional[str]
    last_verdict: Optional[str]
    readiness_score: float
    blockers: List[str]


class ReadinessResponse(BaseModel):
    servers: List[ServerReadiness]
    total: int
    ready_count: int


class ServerReadinessDetail(BaseModel):
    server_id: int
    name: str
    readiness_score: float
    blockers: List[str]
    axis_scores: Dict[str, float]
    open_disputes: int
    last_scanned: Optional[str]


def compute_readiness(
    server: McpServerRegistry,
    latest_score: Optional[McpLlmAxisScore],
    open_dispute_count: int,
) -> tuple[float, List[str]]:
    score = 0.0
    blockers: List[str] = []

    if server.verdict in ("UNTRUSTED", "REJECTED"):
        blockers.append("bad_verdict")
    else:
        score += 40

    if latest_score is None:
        blockers.append("never_scored")
    else:
        p_top = latest_score.p_top or 0.0
        if p_top >= 70:
            score += 40
        elif p_top >= 40:
            score += 20
        else:
            blockers.append("low_p_top")

    if open_dispute_count > 0:
        blockers.append("open_disputes")

    if server.risk_tier in ("CRITICAL", "HIGH"):
        blockers.append("high_risk_tier")
    elif server.risk_tier in ("LOW", "MEDIUM", "TRUSTED"):
        score += 20

    return min(score, 100.0), blockers


@router.get("/promotion/readiness", response_model=ReadinessResponse)
def list_readiness(
    min_score: float = 0.0,
    session: Session = Depends(get_session),
):
    sub_latest = (
        session.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.id).label("max_id"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )
    latest_scores = (
        session.query(McpLlmAxisScore)
        .join(sub_latest, McpLlmAxisScore.id == sub_latest.c.max_id)
        .all()
    )
    latest_map: Dict[int, McpLlmAxisScore] = {s.server_id: s for s in latest_scores}

    open_disputes = (
        session.query(McpScoreDispute.server_id, func.count().label("cnt"))
        .filter(McpScoreDispute.status.in_(("pending", "submitted")))
        .group_by(McpScoreDispute.server_id)
        .all()
    )
    dispute_map: Dict[int, int] = {int(d.server_id): d.cnt for d in open_disputes}

    servers = session.query(McpServerRegistry).all()
    results: List[ServerReadiness] = []
    ready_count = 0

    for srv in servers:
        score, blockers = compute_readiness(
            srv,
            latest_map.get(srv.server_id),
            dispute_map.get(srv.server_id, 0),
        )
        if score < min_score:
            continue
        if not blockers:
            ready_count += 1
        results.append(
            ServerReadiness(
                server_id=srv.server_id,
                name=srv.name,
                risk_tier=srv.risk_tier,
                trust_score=srv.trust_score,
                last_scanned=str(srv.last_scanned) if srv.last_scanned else None,
                last_verdict=srv.verdict,
                readiness_score=score,
                blockers=blockers,
            )
        )

    results.sort(key=lambda r: r.readiness_score, reverse=True)
    return ReadinessResponse(
        servers=results,
        total=len(results),
        ready_count=ready_count,
    )


@router.get("/promotion/readiness/{server_id}", response_model=ServerReadinessDetail)
def server_readiness(
    server_id: int,
    session: Session = Depends(get_session),
):
    srv = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    latest = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.id.desc())
        .first()
    )

    open_disputes = (
        session.query(func.count())
        .filter(
            McpScoreDispute.server_id == server_id,
            McpScoreDispute.status.in_(("pending", "submitted")),
        )
        .scalar()
        or 0
    )

    axis_scores: Dict[str, float] = {}
    if latest:
        axes = (
            session.query(
                McpLlmAxisScore.axis_name,
                func.max(McpLlmAxisScore.p_top).label("p_top"),
            )
            .filter(McpLlmAxisScore.server_id == server_id)
            .group_by(McpLlmAxisScore.axis_name)
            .all()
        )
        axis_scores = {a.axis_name: a.p_top for a in axes}

    score, blockers = compute_readiness(srv, latest, open_disputes)

    return ServerReadinessDetail(
        server_id=server_id,
        name=srv.name,
        readiness_score=score,
        blockers=blockers,
        axis_scores=axis_scores,
        open_disputes=open_disputes,
        last_scanned=str(srv.last_scanned) if srv.last_scanned else None,
    )


if __name__ == "__main__":
    import datetime
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    test_session = SessionLocal()

    fastapi_app = FastAPI()
    fastapi_app.dependency_overrides[get_session] = lambda: test_session
    fastapi_app.include_router(router)

    # Seed test data
    srv1 = McpServerRegistry(
        server_id=1, name="good-srv", verdict="TRUSTED_GENERAL",
        risk_tier="LOW", trust_score=0.9, last_scanned=datetime.datetime.utcnow(),
    )
    srv2 = McpServerRegistry(
        server_id=2, name="bad-srv", verdict="UNTRUSTED",
        risk_tier="CRITICAL", trust_score=0.1, last_scanned=datetime.datetime.utcnow(),
    )
    srv3 = McpServerRegistry(
        server_id=3, name="unscored-srv", verdict="TRUSTED_GENERAL",
        risk_tier="MEDIUM", trust_score=0.5, last_scanned=None,
    )
    test_session.add_all([srv1, srv2, srv3])
    test_session.commit()

    score1 = McpLlmAxisScore(
        id=1, server_id=1, axis_name="overall_risk",
        label="LOW", p_top=80.0, scored_at=datetime.datetime.utcnow(),
    )
    score2 = McpLlmAxisScore(
        id=2, server_id=2, axis_name="overall_risk",
        label="CRITICAL", p_top=15.0, scored_at=datetime.datetime.utcnow(),
    )
    test_session.add_all([score1, score2])
    test_session.commit()

    client = TestClient(fastapi_app)

    r1 = client.get("/promotion/readiness")
    assert r1.status_code == 200, r1.text
    data = r1.json()
    assert data["total"] == 3, str(data)
    assert any(s["name"] == "good-srv" for s in data["servers"]), data

    r2 = client.get("/promotion/readiness/1")
    assert r2.status_code == 200, r2.text
    d = r2.json()
    assert d["server_id"] == 1
    assert d["readiness_score"] > 0

    r3 = client.get("/promotion/readiness?min_score=100")
    assert r3.status_code == 200, r3.text
    data3 = r3.json()
    assert data3["ready_count"] == 0, str(data3)

    r4 = client.get("/promotion/readiness/999")
    assert r4.status_code == 404, r4.text

    print("PASS")
