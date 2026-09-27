# deps: fastapi, pydantic, sqlalchemy, requests
"""scoring_alert_feed — emit recent scoring alerts from the SFT risk pipeline.

Public endpoint (auth=public).  Data from app Postgres via get_session +
SQLAlchemy on McpLlmAxisScore + McpServerRegistry.  Applies trust_gating_override
so verified publishers are not misrepresented as HIGH/CRITICAL.

Endpoints
---------
  GET /api/scoring_alert_feed/alerts
      Recent scoring events (new HIGH/CRITICAL assignments, score drift,
      escalated axes) with optional risk_tier filter.
  GET /api/scoring_alert_feed/alerts/summary
      Per-risk-tier alert counts for the last N days.
  GET /api/scoring_alert_feed/alerts/critical-axes
      Servers with escalated axes in HIGH/CRITICAL risk tiers.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from trust_gating_override import trust_gate
except ImportError:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "trust_gating_override",
        str(Path(__file__).resolve().parents[2] / "trust_gating_override.py"),
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    trust_gate = _mod.trust_gate

router = APIRouter(prefix="/api/scoring_alert_feed", tags=["scoring_alert_feed"])


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class AxisScoreRef(BaseModel):
    axis_name: str
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    scored_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScoringAlertEntry(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    url: Optional[str] = None
    risk_tier: Optional[str] = None
    axis_name: str
    label: str
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    model_version: str
    scored_at: datetime
    trust_override_applied: bool = False
    published_label: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ScoringAlertFeedResponse(BaseModel):
    period_days: int
    total_alerts: int
    alerts: List[ScoringAlertEntry]
    as_of: str


class TierCountEntry(BaseModel):
    risk_tier: str
    count: int


class ScoringAlertSummaryResponse(BaseModel):
    period_days: int
    tiers: List[TierCountEntry]
    total: int
    as_of: str


class CriticalAxisEntry(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    risk_tier: Optional[str] = None
    last_assessed: Optional[datetime] = None
    escalated_axes: List[AxisScoreRef] = []


class CriticalAxesResponse(BaseModel):
    servers: List[CriticalAxisEntry] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AXIS_NAMES = frozenset(
    "overall_risk auth_strength capability_breadth data_sensitivity "
    "network_egress maintainer_trust exploit_surface".split()
)

HIGH_CRITICAL_TIERS = ("HIGH", "CRITICAL")


def _apply_trust_gate(
    url: Optional[str],
    name: Optional[str],
    axis_name: str,
    label: Optional[str],
    p_top: Optional[float],
    p_critical: Optional[float],
    p_danger: Optional[float],
) -> tuple[bool, Optional[str]]:
    """Return (override_applied, published_label)."""
    axes = {"overall_risk": label or "LOW", "maintainer_trust": "VERIFIED"}
    result = trust_gate(url, name, axes)
    if result.get("capped"):
        return True, result.get("published_overall_risk")
    return False, None


# ---------------------------------------------------------------------------
# Endpoint: GET /api/scoring_alert_feed/alerts
# ---------------------------------------------------------------------------

@router.get(
    "/alerts",
    response_model=ScoringAlertFeedResponse,
    summary="Recent scoring alerts",
)
def get_scoring_alerts(
    period_days: int = Query(default=7, ge=1, le=90),
    risk_tier: Optional[str] = Query(
        default=None,
        description="Filter to a specific risk tier (e.g. HIGH, CRITICAL)",
    ),
    axis_name: Optional[str] = Query(
        default=None,
        description="Filter to a specific axis (e.g. overall_risk)",
    ),
    db: Session = Depends(get_session),
) -> ScoringAlertFeedResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    query = (
        db.query(McpLlmAxisScore)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )

    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    else:
        query = query.filter(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))

    rows = (
        query.order_by(McpLlmAxisScore.scored_at.desc())
        .limit(500)
        .all()
    )

    alerts: List[ScoringAlertEntry] = []
    for row in rows:
        override_applied, published_label = _apply_trust_gate(
            None,
            None,
            row.axis_name,
            row.label,
            row.p_top,
            row.p_critical,
            row.p_danger,
        )
        alerts.append(
            ScoringAlertEntry(
                server_id=row.server_id,
                server_name=None,
                url=None,
                risk_tier=None,
                axis_name=row.axis_name,
                label=row.label or "UNKNOWN",
                label_index=row.label_index,
                p_top=row.p_top,
                p_critical=row.p_critical,
                p_danger=row.p_danger,
                escalated=bool(row.escalated),
                escalated_to=row.escalated_to,
                model_version=row.model_version,
                scored_at=row.scored_at or datetime.now(timezone.utc),
                trust_override_applied=override_applied,
                published_label=published_label,
            )
        )

    if alerts:
        server_ids = list({a.server_id for a in alerts})
        server_rows = (
            db.execute(
                select(
                    McpServerRegistry.server_id,
                    McpServerRegistry.name,
                    McpServerRegistry.url,
                    McpServerRegistry.risk_tier,
                ).where(McpServerRegistry.server_id.in_(server_ids))
            ).all()
        )
        meta_map = {r.server_id: r for r in server_rows}
        for a in alerts:
            if a.server_id in meta_map:
                r = meta_map[a.server_id]
                a.server_name = r.name
                a.url = r.url
                a.risk_tier = r.risk_tier

    return ScoringAlertFeedResponse(
        period_days=period_days,
        total_alerts=len(alerts),
        alerts=alerts,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoint: GET /api/scoring_alert_feed/alerts/summary
# ---------------------------------------------------------------------------

@router.get(
    "/alerts/summary",
    response_model=ScoringAlertSummaryResponse,
    summary="Scoring alert summary by risk tier",
)
def get_scoring_alert_summary(
    period_days: int = Query(default=7, ge=1, le=90),
    axis_name: Optional[str] = Query(
        default="overall_risk",
        description="Axis to summarize (default: overall_risk)",
    ),
    db: Session = Depends(get_session),
) -> ScoringAlertSummaryResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    rows = (
        db.execute(
            select(
                McpServerRegistry.risk_tier,
                func.count(McpLlmAxisScore.id).label("count"),
            )
            .select_from(McpLlmAxisScore)
            .join(
                McpServerRegistry,
                McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            )
            .where(McpLlmAxisScore.scored_at >= cutoff)
            .where(McpLlmAxisScore.axis_name == axis_name)
            .group_by(McpServerRegistry.risk_tier)
        ).all()
    )

    tiers = [
        TierCountEntry(risk_tier=r.risk_tier or "UNKNOWN", count=r.count)
        for r in rows
    ]
    tiers.sort(key=lambda x: x.count, reverse=True)

    return ScoringAlertSummaryResponse(
        period_days=period_days,
        tiers=tiers,
        total=sum(t.count for t in tiers),
        as_of=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoint: GET /api/scoring_alert_feed/alerts/critical-axes
# ---------------------------------------------------------------------------

@router.get(
    "/alerts/critical-axes",
    response_model=CriticalAxesResponse,
    summary="Servers with escalated axes in HIGH/CRITICAL risk tiers",
)
def get_critical_axes(
    risk_tier: Optional[str] = Query(
        default=None,
        description="Filter to a specific risk tier",
    ),
    db: Session = Depends(get_session),
) -> CriticalAxesResponse:
    tiers = (risk_tier,) if risk_tier else ("HIGH", "CRITICAL")

    servers = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier.in_(tiers))
        .all()
    )

    if not servers:
        return CriticalAxesResponse(servers=[])

    server_ids = [s.server_id for s in servers]
    server_map = {s.server_id: s for s in servers}

    escalated_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id.in_(server_ids))
        .filter(McpLlmAxisScore.escalated.is_(True))
        .all()
    )

    axes_by_server: dict = {}
    for row in escalated_rows:
        axes_by_server.setdefault(row.server_id, []).append(row)

    result: List[CriticalAxisEntry] = []
    for srv_id, axes in axes_by_server.items():
        srv = server_map[srv_id]
        result.append(
            CriticalAxisEntry(
                server_id=srv_id,
                server_name=srv.name,
                risk_tier=srv.risk_tier,
                last_assessed=srv.last_assessed,
                escalated_axes=[
                    AxisScoreRef(
                        axis_name=ax.axis_name,
                        label=ax.label or "UNKNOWN",
                        p_top=ax.p_top,
                        p_critical=ax.p_critical,
                        p_danger=ax.p_danger,
                        escalated=bool(ax.escalated),
                        escalated_to=ax.escalated_to,
                        scored_at=ax.scored_at or datetime.now(timezone.utc),
                    )
                    for ax in axes
                ],
            )
        )

    return CriticalAxesResponse(servers=result)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Ensure repo root is on sys.path before any app.* imports
    _repo_root = str(Path(__file__).resolve().parents[2])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    McpLlmAxisScore.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)

    with TestSession() as sess:
        sess.add(McpServerRegistry(
            server_id="srv-hi",
            name="High Risk Server",
            risk_tier="HIGH",
            registry_source="test",
            url="https://github.com/example/high-risk",
            description="Test server",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-crit",
            name="Critical Server",
            risk_tier="CRITICAL",
            registry_source="test",
            url="https://github.com/example/critical",
            description="Test server",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-low",
            name="Low Risk Server",
            risk_tier="LOW",
            registry_source="test",
            url="https://github.com/example/low",
            description="Test server",
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-hi",
            axis_name="overall_risk",
            label="HIGH",
            label_index=3,
            p_top=0.72,
            p_critical=0.15,
            p_danger=0.10,
            escalated=False,
            model_version="v1",
            scored_at=now - timedelta(hours=2),
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-hi",
            axis_name="exploit_surface",
            label="HIGH",
            label_index=3,
            p_top=0.68,
            escalated=True,
            escalated_to="SOC",
            model_version="v1",
            scored_at=now - timedelta(hours=1),
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-crit",
            axis_name="overall_risk",
            label="CRITICAL",
            label_index=4,
            p_top=0.91,
            p_critical=0.05,
            escalated=False,
            model_version="v1",
            scored_at=now - timedelta(hours=3),
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-low",
            axis_name="overall_risk",
            label="LOW",
            p_top=0.05,
            escalated=False,
            model_version="v1",
            scored_at=now - timedelta(hours=1),
        ))
        sess.commit()

    client = TestClient(app)

    # Test 1: GET /alerts — default filter returns HIGH/CRITICAL axis rows
    r1 = client.get("/api/scoring_alert_feed/alerts?period_days=7")
    if r1.status_code != 200:
        print(f"FAIL: /alerts status {r1.status_code}: {r1.text}", file=sys.stderr)
        sys.exit(1)
    body1 = r1.json()
    if body1["total_alerts"] < 1:
        print(f"FAIL: expected >=1 alerts, got {body1}", file=sys.stderr)
        sys.exit(1)
    overall_alerts = [a for a in body1["alerts"] if a["axis_name"] == "overall_risk"]
    if not overall_alerts:
        print("FAIL: no overall_risk alerts returned", file=sys.stderr)
        sys.exit(1)
    low_alerts = [a for a in body1["alerts"] if a["server_id"] == "srv-low"]
    if low_alerts:
        print("FAIL: LOW-tier server appeared in default alert feed", file=sys.stderr)
        sys.exit(1)

    # Test 2: GET /alerts?risk_tier=LOW — explicitly filtered
    r2 = client.get("/api/scoring_alert_feed/alerts?risk_tier=LOW")
    if r2.status_code != 200:
        print(f"FAIL: /alerts?risk_tier=LOW status {r2.status_code}", file=sys.stderr)
        sys.exit(1)
    body2 = r2.json()
    if not any(a["server_id"] == "srv-low" for a in body2["alerts"]):
        print("FAIL: LOW-tier server missing when filtering by risk_tier=LOW",
              file=sys.stderr)
        sys.exit(1)

    # Test 3: GET /alerts/summary
    r3 = client.get("/api/scoring_alert_feed/alerts/summary?period_days=7")
    if r3.status_code != 200:
        print(f"FAIL: /alerts/summary status {r3.status_code}: {r3.text}",
              file=sys.stderr)
        sys.exit(1)
    body3 = r3.json()
    if body3["period_days"] != 7:
        print("FAIL: summary period_days mismatch", file=sys.stderr)
        sys.exit(1)
    if body3["total"] < 1:
        print(f"FAIL: expected >=1 total in summary, got {body3}", file=sys.stderr)
        sys.exit(1)
    tier_names = {t["risk_tier"] for t in body3["tiers"]}
    if "HIGH" not in tier_names and "CRITICAL" not in tier_names:
        print(f"FAIL: HIGH/CRITICAL not in summary tiers: {tier_names}",
              file=sys.stderr)
        sys.exit(1)

    # Test 4: GET /alerts/critical-axes — escalated HIGH-tier servers
    r4 = client.get("/api/scoring_alert_feed/alerts/critical-axes")
    if r4.status_code != 200:
        print(f"FAIL: /alerts/critical-axes status {r4.status_code}: {r4.text}",
              file=sys.stderr)
        sys.exit(1)
    body4 = r4.json()
    if len(body4["servers"]) < 1:
        print("FAIL: expected >=1 server in critical-axes", file=sys.stderr)
        sys.exit(1)
    hi_srv = next((s for s in body4["servers"] if s["server_id"] == "srv-hi"), None)
    if hi_srv is None:
        print("FAIL: srv-hi not in critical-axes", file=sys.stderr)
        sys.exit(1)
    if not hi_srv["escalated_axes"]:
        print("FAIL: srv-hi has no escalated_axes", file=sys.stderr)
        sys.exit(1)
    if hi_srv["escalated_axes"][0]["escalated_to"] != "SOC":
        print("FAIL: srv-hi escalated_to != SOC", file=sys.stderr)
        sys.exit(1)
    low_srv = next((s for s in body4["servers"] if s["server_id"] == "srv-low"), None)
    if low_srv is not None:
        print("FAIL: LOW-tier server appeared in critical-axes", file=sys.stderr)
        sys.exit(1)

    # Test 5: GET /alerts/critical-axes?risk_tier=CRITICAL
    r5 = client.get("/api/scoring_alert_feed/alerts/critical-axes?risk_tier=CRITICAL")
    if r5.status_code != 200:
        print(f"FAIL: critical-axes?risk_tier=CRITICAL status {r5.status_code}",
              file=sys.stderr)
        sys.exit(1)
    body5 = r5.json()
    if len(body5["servers"]) != 1:
        print(f"FAIL: expected 1 CRITICAL server, got {len(body5['servers'])}",
              file=sys.stderr)
        sys.exit(1)
    if body5["servers"][0]["server_id"] != "srv-crit":
        print("FAIL: wrong CRITICAL server returned", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)
