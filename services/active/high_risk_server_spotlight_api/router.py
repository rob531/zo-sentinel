# deps: fastapi, pydantic, sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["high_risk_server_spotlight_api"])


# ---------- Pydantic models ----------

class AxisScoreDetail(BaseModel):
    axis_name: str
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    escalated_to: Optional[str] = None


class ServerSpotlightEntry(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: str
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    trust_score: Optional[float] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    last_assessed: Optional[str] = None
    last_seen: Optional[str] = None
    scan_count: Optional[int] = None
    top_axis: Optional[AxisScoreDetail] = None


class SpotlightListResponse(BaseModel):
    servers: List[ServerSpotlightEntry]
    total: int
    page: int
    page_size: int


class SpotlightSummaryResponse(BaseModel):
    total_servers: int
    by_tier: dict


# ---------- Helpers ----------

HIGH_RISK_TIERS = ("HIGH_RISK_ISOLATED", "KNOWN_THREAT")


# ---------- Endpoints ----------

@router.get(
    "/servers/spotlight",
    response_model=SpotlightListResponse,
    name="high_risk_server_spotlight",
)
def get_spotlight(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tier: Optional[str] = Query(
        None,
        description="Filter by tier: HIGH_RISK_ISOLATED or KNOWN_THREAT",
    ),
    db: Session = Depends(get_session),
) -> SpotlightListResponse:
    """Paginated list of high-risk servers, sorted by highest combined danger score."""
    tiers = [tier.upper()] if tier else list(HIGH_RISK_TIERS)

    base_q = db.query(McpServerRegistry).filter(
        McpServerRegistry.risk_tier.in_(tiers)
    )
    total = base_q.count()

    items = (
        base_q.order_by(McpServerRegistry.last_assessed.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    servers: List[ServerSpotlightEntry] = []
    for item in items:
        # Fetch the top (highest p_danger + p_critical) axis score for this server
        top_axis_row = (
            db.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == item.server_id)
            .order_by(
                (func.coalesce(McpLlmAxisScore.p_danger, 0) +
                 func.coalesce(McpLlmAxisScore.p_critical, 0)).desc()
            )
            .first()
        )
        top_axis: Optional[AxisScoreDetail] = None
        if top_axis_row:
            top_axis = AxisScoreDetail(
                axis_name=top_axis_row.axis_name,
                label=top_axis_row.label,
                p_top=float(top_axis_row.p_top) if top_axis_row.p_top is not None else None,
                p_critical=float(top_axis_row.p_critical) if top_axis_row.p_critical is not None else None,
                p_danger=float(top_axis_row.p_danger) if top_axis_row.p_danger is not None else None,
                escalated=top_axis_row.escalated,
                escalated_to=top_axis_row.escalated_to,
            )

        servers.append(
            ServerSpotlightEntry(
                server_id=item.server_id,
                name=item.name,
                risk_tier=item.risk_tier,
                verdict=item.verdict,
                confidence=float(item.confidence) if item.confidence is not None else None,
                trust_score=float(item.trust_score) if item.trust_score is not None else None,
                registry_source=item.registry_source,
                url=item.url,
                description=item.description,
                last_assessed=str(item.last_assessed) if item.last_assessed else None,
                last_seen=str(item.last_seen) if item.last_seen else None,
                scan_count=item.scan_count,
                top_axis=top_axis,
            )
        )

    return SpotlightListResponse(
        servers=servers,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/servers/spotlight/summary",
    response_model=SpotlightSummaryResponse,
    name="high_risk_server_spotlight_summary",
)
def get_spotlight_summary(
    db: Session = Depends(get_session),
) -> SpotlightSummaryResponse:
    """Aggregate counts of high-risk servers broken down by tier."""
    total = (
        db.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.risk_tier.in_(HIGH_RISK_TIERS))
        .scalar()
        or 0
    )
    by_tier: dict = {}
    for tier in HIGH_RISK_TIERS:
        by_tier[tier] = (
            db.query(func.count(McpServerRegistry.server_id))
            .filter(McpServerRegistry.risk_tier == tier)
            .scalar()
            or 0
        )
    return SpotlightSummaryResponse(total_servers=total, by_tier=by_tier)


@router.get(
    "/servers/spotlight/{server_id}",
    response_model=ServerSpotlightEntry,
    name="high_risk_server_spotlight_detail",
)
def get_spotlight_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerSpotlightEntry:
    """Detail for a specific server — returns 404 if the server is not high-risk."""
    item = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    if item.risk_tier not in HIGH_RISK_TIERS:
        raise HTTPException(status_code=404, detail=f"Server {server_id} is not high-risk")

    top_axis_row = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == item.server_id)
        .order_by(
            (func.coalesce(McpLlmAxisScore.p_danger, 0) +
             func.coalesce(McpLlmAxisScore.p_critical, 0)).desc()
        )
        .first()
    )
    top_axis: Optional[AxisScoreDetail] = None
    if top_axis_row:
        top_axis = AxisScoreDetail(
            axis_name=top_axis_row.axis_name,
            label=top_axis_row.label,
            p_top=float(top_axis_row.p_top) if top_axis_row.p_top is not None else None,
            p_critical=float(top_axis_row.p_critical) if top_axis_row.p_critical is not None else None,
            p_danger=float(top_axis_row.p_danger) if top_axis_row.p_danger is not None else None,
            escalated=top_axis_row.escalated,
            escalated_to=top_axis_row.escalated_to,
        )

    return ServerSpotlightEntry(
        server_id=item.server_id,
        name=item.name,
        risk_tier=item.risk_tier,
        verdict=item.verdict,
        confidence=float(item.confidence) if item.confidence is not None else None,
        trust_score=float(item.trust_score) if item.trust_score is not None else None,
        registry_source=item.registry_source,
        url=item.url,
        description=item.description,
        last_assessed=str(item.last_assessed) if item.last_assessed else None,
        last_seen=str(item.last_seen) if item.last_seen else None,
        scan_count=item.scan_count,
        top_axis=top_axis,
    )


# ---------- Self-test ----------
if __name__ == "__main__":
    import datetime as dt
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parents[3]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = get_test_session

    # Seed data
    now = dt.datetime.utcnow()
    # Use naive datetime for SQLite compatibility
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        srv1 = McpServerRegistry(
            server_id="spot1",
            name="ThreatServer",
            risk_tier="KNOWN_THREAT",
            verdict="malicious",
            confidence=0.95,
            trust_score=5.0,
            registry_source="community",
            url="https://bad.example.com",
            description="Known malicious server",
            last_assessed=now,
            last_seen=now,
            scan_count=3,
        )
        srv2 = McpServerRegistry(
            server_id="spot2",
            name="HighRiskIsolated",
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="suspicious",
            confidence=0.80,
            trust_score=20.0,
            registry_source="community",
            url="https://suspicious.example.com",
            description="High risk isolated server",
            last_assessed=now,
            last_seen=now,
            scan_count=1,
        )
        srv3 = McpServerRegistry(
            server_id="safe1",
            name="SafeServer",
            risk_tier="LOW_RISK",
            verdict="trusted",
            confidence=0.99,
            trust_score=95.0,
            registry_source="official",
            url="https://safe.example.com",
            description="Safe server",
            last_assessed=now,
            last_seen=now,
            scan_count=50,
        )
        db.add_all([srv1, srv2, srv3])

        # Axis scores for spot1
        for axis_name in ("overall_risk", "auth_strength", "capability_breadth"):
            db.add(
                McpLlmAxisScore(
                    id=1 if axis_name == "overall_risk" else 2 if axis_name == "auth_strength" else 3,
                    server_id="spot1",
                    axis_name=axis_name,
                    label="critical" if axis_name == "overall_risk" else "medium",
                    label_index=3 if axis_name == "overall_risk" else 1,
                    p_top=0.05,
                    p_critical=0.90 if axis_name == "overall_risk" else 0.10,
                    p_danger=0.05,
                    escalated=axis_name == "overall_risk",
                    escalated_to="KNOWN_THREAT" if axis_name == "overall_risk" else None,
                    model_version="v1",
                    adapter_sha256="abc123",
                    scored_at=now,
                )
            )
        db.commit()

    client = TestClient(that_app)

    # --- Test list endpoint ---
    resp = client.get("/api/servers/spotlight")
    assert resp.status_code == 200, f"list status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["total"] == 2, f"total mismatch: {data['total']}"
    assert len(data["servers"]) == 2, f"count mismatch: {len(data['servers'])}"
    tiers = {s["risk_tier"] for s in data["servers"]}
    assert tiers == {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}, f"tiers: {tiers}"
    # spot1 should have a top_axis (KNOWN_THREAT has highest danger score)
    spot1_entry = next(s for s in data["servers"] if s["server_id"] == "spot1")
    assert spot1_entry["top_axis"] is not None, "spot1 should have top_axis"
    assert spot1_entry["top_axis"]["axis_name"] == "overall_risk"

    # --- Test summary endpoint ---
    resp = client.get("/api/servers/spotlight/summary")
    assert resp.status_code == 200, f"summary status {resp.status_code}"
    summary = resp.json()
    assert summary["total_servers"] == 2, f"summary total: {summary['total_servers']}"
    assert summary["by_tier"]["HIGH_RISK_ISOLATED"] == 1
    assert summary["by_tier"]["KNOWN_THREAT"] == 1

    # --- Test detail for a high-risk server ---
    resp = client.get("/api/servers/spotlight/spot1")
    assert resp.status_code == 200, f"detail status {resp.status_code}"
    detail = resp.json()
    assert detail["server_id"] == "spot1"
    assert detail["risk_tier"] == "KNOWN_THREAT"
    assert detail["top_axis"] is not None

    # --- Test detail for a non-high-risk server -> 404 ---
    resp = client.get("/api/servers/spotlight/safe1")
    assert resp.status_code == 404, f"safe1 should 404, got {resp.status_code}"

    # --- Test 404 for unknown server ---
    resp = client.get("/api/servers/spotlight/nonexistent")
    assert resp.status_code == 404, f"nonexistent should 404, got {resp.status_code}"

    # --- Test tier filter ---
    resp = client.get("/api/servers/spotlight?tier=KNOWN_THREAT")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert all(s["risk_tier"] == "KNOWN_THREAT" for s in data["servers"])

    print("PASS")
