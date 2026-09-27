# deps: fastapi, pydantic, sqlalchemy
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_compare_dashboard_api"])


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #
class AxisScoreData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: str | None = None
    label_index: int | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated: bool | None = None
    model_version: str | None = None
    scored_at: str | None = None


class ServerBasicData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: str | None = None
    registry_source: str | None = None
    url: str | None = None
    description: str | None = None
    risk_tier: str | None = None
    verdict: str | None = None
    verdict_reasoning: str | None = None
    confidence: float | None = None
    trust_score: float | None = None
    scan_count: int | None = None
    last_assessed: str | None = None
    last_scanned: str | None = None


class AxisDelta(BaseModel):
    label_delta: str | None = None
    p_top_delta: float | None = None


class TierDelta(BaseModel):
    tier_change: int | None = None
    from_tier: str | None = None
    to_tier: str | None = None


class ServerComparePayload(BaseModel):
    server1: ServerBasicData
    server2: ServerBasicData
    server1_axes: dict[str, AxisScoreData]
    server2_axes: dict[str, AxisScoreData]
    tier_delta: TierDelta
    axis_deltas: dict[str, AxisDelta]


class ServerCompareResponse(BaseModel):
    server1_id: str
    server2_id: str
    payload: ServerComparePayload


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _fmt(val) -> str | None:
    if val is None:
        return None
    return str(val)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get(
    "/server-compare/{server1_id}/{server2_id}",
    response_model=ServerCompareResponse,
)
def compare_servers(
    server1_id: str,
    server2_id: str,
    db: Session = Depends(get_session),
) -> ServerCompareResponse:
    """
    Compare two servers by their risk tiers and LLM axis scores.

    Returns basic server metadata, per-axis scores for both servers, and
    computed deltas (tier delta, per-axis p_top delta) so callers can
    render a side-by-side comparison.
    """
    # Load both servers
    s1 = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server1_id).one_or_none()
    s2 = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server2_id).one_or_none()

    if s1 is None:
        raise HTTPException(status_code=404, detail=f"Server '{server1_id}' not found")
    if s2 is None:
        raise HTTPException(status_code=404, detail=f"Server '{server2_id}' not found")

    # Load axis scores for both servers
    axes1_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server1_id)
        .all()
    )
    axes2_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server2_id)
        .all()
    )

    # Build axis dicts
    s1_axes: dict[str, AxisScoreData] = {}
    s2_axes: dict[str, AxisScoreData] = {}
    for row in axes1_rows:
        s1_axes[row.axis_name] = AxisScoreData(
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=row.escalated,
            model_version=row.model_version,
            scored_at=_fmt(row.scored_at),
        )
    for row in axes2_rows:
        s2_axes[row.axis_name] = AxisScoreData(
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=row.escalated,
            model_version=row.model_version,
            scored_at=_fmt(row.scored_at),
        )

    # Build basic data
    server1_basic = ServerBasicData(
        server_id=s1.server_id,
        name=s1.name,
        registry_source=s1.registry_source,
        url=s1.url,
        description=s1.description,
        risk_tier=s1.risk_tier,
        verdict=s1.verdict,
        verdict_reasoning=s1.verdict_reasoning,
        confidence=s1.confidence,
        trust_score=s1.trust_score,
        scan_count=s1.scan_count,
        last_assessed=_fmt(s1.last_assessed),
        last_scanned=_fmt(s1.last_scanned),
    )
    server2_basic = ServerBasicData(
        server_id=s2.server_id,
        name=s2.name,
        registry_source=s2.registry_source,
        url=s2.url,
        description=s2.description,
        risk_tier=s2.risk_tier,
        verdict=s2.verdict,
        verdict_reasoning=s2.verdict_reasoning,
        confidence=s2.confidence,
        trust_score=s2.trust_score,
        scan_count=s2.scan_count,
        last_assessed=_fmt(s2.last_assessed),
        last_scanned=_fmt(s2.last_scanned),
    )

    # Compute tier delta
    t1 = _safe_int(s1.risk_tier)
    t2 = _safe_int(s2.risk_tier)
    tier_delta = TierDelta(
        tier_change=t2 - t1 if t1 is not None and t2 is not None else None,
        from_tier=s1.risk_tier,
        to_tier=s2.risk_tier,
    )

    # Compute per-axis deltas
    axis_deltas: dict[str, AxisDelta] = {}
    for axis_name, s2_axis in s2_axes.items():
        if axis_name in s1_axes:
            s1_axis = s1_axes[axis_name]
            p_delta = (s2_axis.p_top - s1_axis.p_top) if (s1_axis.p_top is not None and s2_axis.p_top is not None) else None
            axis_deltas[axis_name] = AxisDelta(
                label_delta=s2_axis.label,
                p_top_delta=p_delta,
            )

    payload = ServerComparePayload(
        server1=server1_basic,
        server2=server2_basic,
        server1_axes=s1_axes,
        server2_axes=s2_axes,
        tier_delta=tier_delta,
        axis_deltas=axis_deltas,
    )

    return ServerCompareResponse(
        server1_id=server1_id,
        server2_id=server2_id,
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from datetime import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite for self-test only
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Seed test data
    with TestSession() as sess:
        now = datetime.utcnow()
        s1 = McpServerRegistry(
            server_id="srv-a",
            name="Alpha Server",
            risk_tier="2",
            confidence=0.95,
            trust_score=0.88,
            verdict="TRUSTED_GENERAL",
            verdict_reasoning="All checks passed",
            registry_source="npx",
            url="https://example.com/alpha",
            description="Alpha test server",
            scan_count=3,
            last_assessed=now,
            last_scanned=now,
            first_seen=now,
            last_seen=now,
            meta=None,
        )
        s2 = McpServerRegistry(
            server_id="srv-b",
            name="Beta Server",
            risk_tier="4",
            confidence=0.75,
            trust_score=0.45,
            verdict="ELEVATED_RISK",
            verdict_reasoning="Some concerns",
            registry_source="npx",
            url="https://example.com/beta",
            description="Beta test server",
            scan_count=1,
            last_assessed=now,
            last_scanned=now,
            first_seen=now,
            last_seen=now,
            meta=None,
        )
        sess.add_all([s1, s2])

        a1 = McpLlmAxisScore(
            server_id="srv-a",
            axis_name="overall_risk",
            label="low",
            label_index=1,
            p_top=0.15,
            p_critical=0.02,
            p_danger=0.08,
            escalated=False,
            model_version="v2",
            adapter_sha256="a" * 64,
            probs={},
            scored_at=now,
        )
        a2 = McpLlmAxisScore(
            server_id="srv-b",
            axis_name="overall_risk",
            label="medium",
            label_index=2,
            p_top=0.52,
            p_critical=0.10,
            p_danger=0.25,
            escalated=False,
            model_version="v2",
            adapter_sha256="b" * 64,
            probs={},
            scored_at=now,
        )
        sess.add_all([a1, a2])
        sess.commit()

    # Build test app with dependency override
    app = FastAPI()
    app.include_router(router)

    def _override():
        return TestSession()

    from app.main import app as main_app
    main_app.dependency_overrides[get_session] = _override

    client = TestClient(main_app)

    # Happy path: compare two existing servers
    resp = client.get("/api/server-compare/srv-a/srv-b")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["server1_id"] == "srv-a"
    assert data["server2_id"] == "srv-b"
    assert data["payload"]["server1"]["name"] == "Alpha Server"
    assert data["payload"]["server2"]["name"] == "Beta Server"
    # Tier delta: int(4) - int(2) = 2
    assert data["payload"]["tier_delta"]["tier_change"] == 2, \
        f"Expected tier_change=2, got {data['payload']['tier_delta']}"
    # p_top delta: 0.52 - 0.15 = 0.37
    assert abs(data["payload"]["axis_deltas"]["overall_risk"]["p_top_delta"] - 0.37) < 1e-6, \
        f"p_top delta mismatch: {data['payload']['axis_deltas']}"

    # 404 on missing server
    resp404 = client.get("/api/server-compare/srv-a/nonexistent")
    assert resp404.status_code == 404, f"Expected 404, got {resp404.status_code}"

    main_app.dependency_overrides.clear()
    print("PASS")
