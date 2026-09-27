# deps: fastapi, sqlalchemy, pydantic
"""Axis Volatility Scoring Consumer.

Reads mcp_llm_axis_scores WHERE axis_name='overall_risk' (app Postgres via get_session),
groups by server_id, computes Shannon entropy of p_top across >=3 most-recent scores,
writes one axis_volatility row per qualifying server to mcp_llm_axis_scores.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models (McpLlmAxisScore, McpServerRegistry).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_volatility_scoring_consumer"])

# --------------------------------------------------------------------------- #
# Compute helpers
# --------------------------------------------------------------------------- #

def _shannon_entropy(values: List[float]) -> float:
    """Shannon entropy of a normalised probability list."""
    total = sum(values)
    if total == 0:
        return 0.0
    entropy = 0.0
    for v in values:
        p = v / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _entropy_score(p_top_values: List[float]) -> float:
    """Return entropy as a 0-100 score (higher = more volatile)."""
    return round(_shannon_entropy(p_top_values) * 100, 4)


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class ServerVolatilityRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    server_id: str
    name: Optional[str] = None
    p_top: float
    entropy_score: float
    score_count: int
    written: bool = False


class ComputeResponse(BaseModel):
    computed_count: int
    skipped_count: int
    records: List[ServerVolatilityRecord]
    computed_at: datetime


class ServerVolatilityGetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    server_id: str
    name: Optional[str]
    volatility_score: float
    p_top: float
    score_count: int
    latest_scored_at: Optional[datetime]


class HealthResponse(BaseModel):
    status: str
    servers_with_enough_scores: int
    total_servers: int


# --------------------------------------------------------------------------- #
# Core compute logic
# --------------------------------------------------------------------------- #

def _compute_and_write(session: Session) -> Dict[str, Any]:
    """Read overall_risk scores, compute entropy, write axis_volatility rows."""
    cutoff = datetime(2000, 1, 1, tzinfo=timezone.utc)

    # Pull most-recent N scores per server for overall_risk axis
    subq = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.p_top,
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=McpLlmAxisScore.scored_at.desc(),
            )
            .label("rn"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .subquery()
    )

    recent = (
        session.query(subq.c.server_id, subq.c.scored_at, subq.c.p_top)
        .where(subq.c.rn <= 10)   # keep a small window; entropy needs ≥3
        .order_by(subq.c.server_id, subq.c.scored_at.desc())
        .all()
    )

    # Group by server
    by_server: Dict[str, List[float]] = defaultdict(list)
    timestamps: Dict[str, datetime] = {}
    for srv_id, scored_at, p_top in recent:
        if p_top is not None and len(by_server[srv_id]) < 10:
            by_server[srv_id].append(p_top)
        if srv_id not in timestamps or scored_at > timestamps[srv_id]:
            timestamps[srv_id] = scored_at

    computed: List[ServerVolatilityRecord] = []
    skipped = 0

    for srv_id, p_tops in by_server.items():
        if len(p_tops) < 3:
            skipped += 1
            continue

        entropy_score = _entropy_score(p_tops)
        p_top_val = round(sum(p_tops) / len(p_tops), 4)

        # Write axis_volatility row
        now = datetime.now(timezone.utc)
        row = McpLlmAxisScore(
            server_id=srv_id,
            axis_name="axis_volatility",
            p_top=p_top_val,
            label=None,
            label_index=None,
            probs=None,
            p_critical=None,
            p_danger=None,
            escalated=False,
            escalated_to=None,
            model_version="volatility_v1",
            decision_rule_version="rule_v1",
            adapter_sha256=None,
            scored_at=timestamps.get(srv_id, now),
        )
        session.add(row)
        computed.append(
            ServerVolatilityRecord(
                server_id=srv_id,
                p_top=p_top_val,
                entropy_score=entropy_score,
                score_count=len(p_tops),
                written=True,
            )
        )

    session.commit()
    return {"computed": computed, "skipped": skipped}


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.post(
    "/axis_volatility/compute",
    response_model=ComputeResponse,
    summary="Compute and persist axis_volatility scores for all qualifying servers",
)
def compute_all_volatility(
    db: Session = Depends(get_session),
) -> ComputeResponse:
    """
    Reads mcp_llm_axis_scores WHERE axis_name='overall_risk', groups by server_id,
    computes Shannon entropy across the most-recent p_top values, and writes one
    axis_volatility row per server with >=3 scores.

    Returns the count of computed and skipped servers.
    """
    result = _compute_and_write(db)
    return ComputeResponse(
        computed_count=len(result["computed"]),
        skipped_count=result["skipped"],
        records=result["computed"],
        computed_at=datetime.now(timezone.utc),
    )


@router.get(
    "/axis_volatility/servers/{server_id}",
    response_model=ServerVolatilityGetResponse,
    summary="Get the latest axis_volatility score for a server",
    responses={404: {"description": "No axis_volatility score found"}},
)
def get_server_volatility(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerVolatilityGetResponse:
    """Return the most-recent axis_volatility score for a server."""
    srv = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    row = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.axis_name == "axis_volatility")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No axis_volatility score found for server {server_id}",
        )

    # Count contributing overall_risk scores
    score_count = (
        db.query(func.count(McpLlmAxisScore.id))
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .scalar() or 0
    )

    entropy = _shannon_entropy([row.p_top]) if row.p_top else 0.0

    return ServerVolatilityGetResponse(
        server_id=server_id,
        name=srv,
        volatility_score=round(entropy * 100, 4),
        p_top=row.p_top,
        score_count=score_count,
        latest_scored_at=row.scored_at,
    )


@router.get(
    "/axis_volatility/health",
    response_model=HealthResponse,
    summary="Return basic health/coverage stats for the consumer",
)
def volatility_health(db: Session = Depends(get_session)) -> HealthResponse:
    """Return count of servers with >=3 overall_risk scores vs total servers."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    qualifying = (
        db.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .group_by(McpLlmAxisScore.server_id)
        .having(func.count(McpLlmAxisScore.id) >= 3)
        .count()
    )

    return HealthResponse(
        status="ok",
        servers_with_enough_scores=qualifying,
        total_servers=total,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)

    with TestSession() as sess:
        # Servers
        sess.add_all([
            McpServerRegistry(server_id="srv-stable", name="Stable Server"),
            McpServerRegistry(server_id="srv-volatile", name="Volatile Server"),
            McpServerRegistry(server_id="srv-few", name="Too Few Scores"),
        ])

        # Stable: tightly clustered p_tops → low entropy
        for i, p in enumerate([0.91, 0.92, 0.90, 0.89]):
            sess.add(McpLlmAxisScore(
                server_id="srv-stable",
                axis_name="overall_risk",
                label="TRUSTED",
                label_index=4,
                p_top=p,
                model_version="v1",
                scored_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            ))

        # Volatile: spread p_tops → high entropy
        for i, p in enumerate([0.05, 0.50, 0.95, 0.30]):
            sess.add(McpLlmAxisScore(
                server_id="srv-volatile",
                axis_name="overall_risk",
                label="HIGH",
                label_index=2,
                p_top=p,
                model_version="v1",
                scored_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            ))

        # Few scores – not enough for entropy
        for i, p in enumerate([0.40, 0.60]):
            sess.add(McpLlmAxisScore(
                server_id="srv-few",
                axis_name="overall_risk",
                label="MEDIUM",
                label_index=1,
                p_top=p,
                model_version="v1",
                scored_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            ))

        sess.commit()

    client = TestClient(test_app)

    # T1: health
    r = client.get("/api/axis_volatility/health")
    assert r.status_code == 200, f"T1 health: {r.text}"
    d = r.json()
    assert d["status"] == "ok"
    assert d["servers_with_enough_scores"] == 2, f"T1 expected 2 qualifying, got {d}"

    # T2: compute
    r = client.post("/api/axis_volatility/compute")
    assert r.status_code == 200, f"T2 compute: {r.text}"
    d = r.json()
    assert d["computed_count"] == 2, f"T2 expected 2 computed, got {d['computed_count']}"
    assert d["skipped_count"] == 1, f"T2 expected 1 skipped, got {d['skipped_count']}"

    # T3: srv-volatile has higher entropy than srv-stable
    stable_rec = next((x for x in d["records"] if x["server_id"] == "srv-stable"), None)
    volatile_rec = next((x for x in d["records"] if x["server_id"] == "srv-volatile"), None)
    assert stable_rec is not None, "T3: stable record missing"
    assert volatile_rec is not None, "T3: volatile record missing"
    assert volatile_rec["entropy_score"] > stable_rec["entropy_score"], (
        f"T3: volatile ({volatile_rec['entropy_score']}) should have higher entropy than "
        f"stable ({stable_rec['entropy_score']})"
    )

    # T4: get server volatility
    r = client.get("/api/axis_volatility/servers/srv-volatile")
    assert r.status_code == 200, f"T4 get: {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-volatile"
    assert d["volatility_score"] > 0

    # T5: 404 for unknown server
    r = client.get("/api/axis_volatility/servers/unknown-srv")
    assert r.status_code == 404, f"T5 expected 404, got {r.status_code}"

    print("PASS")
    sys.exit(0)
