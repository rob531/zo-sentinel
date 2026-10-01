# deps: requests,fastapi,pydantic,sqlalchemy
"""server_critical_axis_consumer -- detect and surface servers with critical-axis risk.

Polls McpLlmAxisScore rows where escalated=True or p_critical is elevated,
identifies dominant critical axes per server, and exposes a consumer feed via
REST. Optionally queries mcp_signal_scores (mesh/pipeline) via write_service.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models; mesh tier via write_service.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Annotated, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_critical_axis_consumer"])

_WRITE_SERVICE = "http://127.0.0.1:8772"

AXIS_NAMES = frozenset({
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
})

# Thresholds for elevated critical signals
P_CRITICAL_HIGH = 0.5
P_CRITICAL_MEDIUM = 0.25


def _write_service(path: str, payload: dict) -> dict:
    url = f"{_WRITE_SERVICE}{path}"
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class HealthResponse(BaseModel):
    status: str
    service: str = "server_critical_axis_consumer"
    checked_at: str


class CriticalAxisRecord(BaseModel):
    server_id: str
    name: Optional[str] = None
    axis_name: str
    label: Optional[str] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    p_top: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    scored_at: Optional[str] = None


class CriticalServerSummary(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    risk_tier: Optional[str] = None
    dominant_axis: Optional[str] = None
    max_p_critical: Optional[float] = None
    escalated_count: int = 0
    critical_axes: list[str] = []


class ConsumeResponse(BaseModel):
    consumed: int
    critical_servers_found: int
    critical_axis_count: int
    escalated_count: int


class SeverityBreakdown(BaseModel):
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    count: int
    servers: list[str]


class SeveritySummaryResponse(BaseModel):
    as_of: str
    total_critical_servers: int
    breakdown: list[SeverityBreakdown]


class CriticalAxisFeedResponse(BaseModel):
    as_of: str
    total_records: int
    records: list[CriticalAxisRecord]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _severity(p_critical: Optional[float]) -> str:
    if p_critical is None:
        return "LOW"
    if p_critical >= P_CRITICAL_HIGH:
        return "CRITICAL"
    if p_critical >= P_CRITICAL_MEDIUM:
        return "HIGH"
    return "MEDIUM"


def _critical_axes_for_server(
    db: Session,
    server_id: str,
    min_p_critical: float = 0.0,
) -> list[McpLlmAxisScore]:
    return (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.p_critical >= min_p_critical,
        )
        .order_by(McpLlmAxisScore.p_critical.desc())
        .all()
    )


def _critical_servers(
    db: Session,
    min_p_critical: float = P_CRITICAL_MEDIUM,
    include_escalated_only: bool = False,
) -> list[CriticalServerSummary]:
    """Return servers with at least one axis above min_p_critical."""
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.p_critical >= min_p_critical)
        .all()
    )

    server_ids = list({r.server_id for r in rows})
    if not server_ids:
        return []

    servers_map = {
        s.server_id: s
        for s in db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    }

    results: list[CriticalServerSummary] = []
    for sid in server_ids:
        srv = servers_map.get(sid)
        axes = [r for r in rows if r.server_id == sid]

        if include_escalated_only:
            axes = [a for a in axes if a.escalated]

        if not axes:
            continue

        axes_sorted = sorted(axes, key=lambda a: a.p_critical or 0, reverse=True)
        dominant = axes_sorted[0]
        critical_list = [a.axis_name for a in axes_sorted if a.axis_name in AXIS_NAMES]
        max_pc = max((a.p_critical for a in axes), default=None)
        esc_count = sum(1 for a in axes if a.escalated)

        results.append(
            CriticalServerSummary(
                server_id=sid,
                name=srv.name if srv else None,
                url=srv.url if srv else None,
                risk_tier=srv.risk_tier if srv else None,
                dominant_axis=dominant.axis_name if dominant else None,
                max_p_critical=float(max_pc) if max_pc is not None else None,
                escalated_count=esc_count,
                critical_axes=critical_list,
            )
        )
    return results


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/server-critical-axis/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(
        status="ok",
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post(
    "/server-critical-axis/consume",
    response_model=ConsumeResponse,
    summary="Poll critical-axis rows and surface the consumer feed",
)
def consume(
    min_p_critical: Annotated[
        float,
        Query(ge=0.0, le=1.0, description="Minimum p_critical threshold"),
    ] = P_CRITICAL_MEDIUM,
    escalated_only: Annotated[
        bool,
        Query(description="Only include rows with escalated=True"),
    ] = False,
    db: Session = Depends(get_session),
) -> ConsumeResponse:
    """
    Scan McpLlmAxisScore for rows where p_critical >= threshold,
    group by server, and return a summary.
    """
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.p_critical >= min_p_critical)
        .all()
    )

    if escalated_only:
        rows = [r for r in rows if r.escalated]

    critical_server_ids = list({r.server_id for r in rows})
    critical_axis_count = len(rows)
    escalated_count = sum(1 for r in rows if r.escalated)
    critical_servers_found = len(critical_server_ids)

    return ConsumeResponse(
        consumed=len(rows),
        critical_servers_found=critical_servers_found,
        critical_axis_count=critical_axis_count,
        escalated_count=escalated_count,
    )


@router.get(
    "/server-critical-axis/servers",
    response_model=list[CriticalServerSummary],
    summary="List servers with at least one critical axis",
)
def list_critical_servers(
    min_p_critical: Annotated[
        float,
        Query(ge=0.0, le=1.0),
    ] = P_CRITICAL_MEDIUM,
    escalated_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: Session = Depends(get_session),
) -> list[CriticalServerSummary]:
    """Return per-server summaries for servers with elevated p_critical axes."""
    servers = _critical_servers(db, min_p_critical, escalated_only)
    return servers[:limit]


@router.get(
    "/server-critical-axis/feed",
    response_model=CriticalAxisFeedResponse,
    summary="Raw feed of critical axis records",
)
def critical_axis_feed(
    min_p_critical: Annotated[
        float,
        Query(ge=0.0, le=1.0),
    ] = P_CRITICAL_MEDIUM,
    server_id: Annotated[
        Optional[str],
        Query(description="Filter to a specific server_id"),
    ] = None,
    axis_name: Annotated[
        Optional[str],
        Query(description="Filter to a specific axis"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    db: Session = Depends(get_session),
) -> CriticalAxisFeedResponse:
    """Return individual McpLlmAxisScore rows above the critical threshold."""
    q = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.p_critical >= min_p_critical,
    )
    if server_id is not None:
        q = q.filter(McpLlmAxisScore.server_id == server_id)
    if axis_name is not None and axis_name in AXIS_NAMES:
        q = q.filter(McpLlmAxisScore.axis_name == axis_name)

    rows = q.order_by(McpLlmAxisScore.p_critical.desc()).limit(limit).all()

    records = [
        CriticalAxisRecord(
            server_id=r.server_id,
            name=None,
            axis_name=r.axis_name,
            label=r.label,
            p_critical=float(r.p_critical) if r.p_critical is not None else None,
            p_danger=float(r.p_danger) if r.p_danger is not None else None,
            p_top=float(r.p_top) if r.p_top is not None else None,
            escalated=bool(r.escalated) if r.escalated is not None else False,
            escalated_to=r.escalated_to,
            scored_at=r.scored_at.isoformat() if r.scored_at else None,
        )
        for r in rows
    ]

    return CriticalAxisFeedResponse(
        as_of=datetime.now(timezone.utc).isoformat(),
        total_records=len(records),
        records=records,
    )


@router.get(
    "/server-critical-axis/severity-summary",
    response_model=SeveritySummaryResponse,
    summary="Severity breakdown of critical servers",
)
def severity_summary(
    db: Session = Depends(get_session),
) -> SeveritySummaryResponse:
    """Return count of critical servers by severity bucket (CRITICAL/HIGH/MEDIUM)."""
    servers = _critical_servers(db, min_p_critical=0.0, include_escalated_only=False)

    buckets = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
    for srv in servers:
        sev = _severity(srv.max_p_critical)
        if sev in buckets:
            buckets[sev].append(srv.server_id)

    breakdown = [
        SeverityBreakdown(severity=k, count=len(v), servers=v)
        for k, v in buckets.items()
        if v
    ]

    return SeveritySummaryResponse(
        as_of=datetime.now(timezone.utc).isoformat(),
        total_critical_servers=len(servers),
        breakdown=breakdown,
    )


@router.get(
    "/server-critical-axis/signal-scores",
    response_model=dict,
    summary="Query mcp_signal_scores via write_service (mesh/pipeline tier)",
)
def signal_scores(
    server_id: Annotated[
        Optional[str],
        Query(description="Filter by server_id"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """
    Query mcp_signal_scores from the mesh/pipeline store via write_service.
    Returns the raw rows dict; callers handle missing fields gracefully.
    """
    params = {"sql": "SELECT * FROM mcp_signal_scores", "limit": limit}
    conditions = []
    if server_id is not None:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if conditions:
        params["sql"] += " WHERE " + " AND ".join(conditions)
    params["sql"] += " ORDER BY scored_at DESC LIMIT :limit"
    try:
        result = _write_service("/query", params)
        return {"rows": result.get("rows", []) if isinstance(result, dict) else [], "total": len(result.get("rows", [])) if isinstance(result, dict) else 0}
    except requests.exceptions.RequestException:
        return {"rows": [], "total": 0, "error": "write_service unavailable"}


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _run_self_test() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session as _real_gs
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data
    with SessionLocal() as db:
        db.add(McpServerRegistry(
            server_id="crit-srv-001",
            name="Critical Server 1",
            registry_source="test",
            url="https://example.com/1",
            risk_tier="HIGH",
        ))
        db.add(McpServerRegistry(
            server_id="crit-srv-002",
            name="Normal Server",
            registry_source="test",
            url="https://example.com/2",
            risk_tier="LOW",
        ))
        for axis in ["overall_risk", "data_sensitivity", "exploit_surface"]:
            db.add(McpLlmAxisScore(
                server_id="crit-srv-001",
                axis_name=axis,
                label="CRITICAL",
                label_index=3,
                p_top=0.05,
                p_critical=0.72 if axis == "overall_risk" else 0.55,
                p_danger=0.60,
                escalated=True,
                escalated_to="HIGH",
                model_version="v1-test",
                decision_rule_version="r1",
                scored_at=datetime.now(timezone.utc),
            ))
        db.add(McpLlmAxisScore(
            server_id="crit-srv-002",
            axis_name="overall_risk",
            label="LOW",
            label_index=0,
            p_top=0.95,
            p_critical=0.05,
            p_danger=0.10,
            escalated=False,
            model_version="v1-test",
            decision_rule_version="r1",
            scored_at=datetime.now(timezone.utc),
        ))
        db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_real_gs] = _override

    client = TestClient(app)

    # 1. Health
    r = client.get("/api/server-critical-axis/health")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    # 2. Consume
    r = client.post("/api/server-critical-axis/consume?min_p_critical=0.25")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["critical_servers_found"] >= 1
    assert body["critical_axis_count"] >= 3
    assert body["escalated_count"] >= 1

    # 3. Consume escalated-only
    r = client.post("/api/server-critical-axis/consume?escalated_only=true")
    assert r.status_code == 200, r.text
    assert r.json()["escalated_count"] >= 1

    # 4. List critical servers
    r = client.get("/api/server-critical-axis/servers?min_p_critical=0.25")
    assert r.status_code == 200, r.text
    servers = r.json()
    assert len(servers) >= 1
    srv = next((s for s in servers if s["server_id"] == "crit-srv-001"), None)
    assert srv is not None, "crit-srv-001 not in critical servers"
    assert srv["dominant_axis"] == "overall_risk"
    assert srv["escalated_count"] >= 1
    assert "overall_risk" in srv["critical_axes"]

    # 5. Feed filtered by server_id
    r = client.get("/api/server-critical-axis/feed?server_id=crit-srv-001&min_p_critical=0.25")
    assert r.status_code == 200, r.text
    feed = r.json()
    assert feed["total_records"] >= 3
    for rec in feed["records"]:
        assert rec["server_id"] == "crit-srv-001"
        assert rec["p_critical"] >= 0.25

    # 6. Feed filtered by axis_name
    r = client.get("/api/server-critical-axis/feed?axis_name=overall_risk&min_p_critical=0.25")
    assert r.status_code == 200, r.text
    feed2 = r.json()
    for rec in feed2["records"]:
        assert rec["axis_name"] == "overall_risk"

    # 7. Severity summary
    r = client.get("/api/server-critical-axis/severity-summary")
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["total_critical_servers"] >= 1
    breakdown = {b["severity"]: b for b in summary["breakdown"]}
    assert "CRITICAL" in breakdown
    assert "crit-srv-001" in breakdown["CRITICAL"]["servers"]

    # 8. signal-scores (mesh) -- expects write_service unavailable gracefully
    r = client.get("/api/server-critical-axis/signal-scores?limit=10")
    assert r.status_code == 200, r.text
    # Must not 5xx even if write_service is down
    sig = r.json()
    assert "rows" in sig

    return True


if __name__ == "__main__":
    try:
        ok = _run_self_test()
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    if ok:
        print("PASS")
        sys.exit(0)
