# deps: fastapi, pydantic, sqlalchemy
"""Router for verdict_distribution -- aggregated distribution of verdicts and axis labels across MCP servers."""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict_distribution"])


# --- Pydantic models -------------------------------------------------------

class AxisDistributionItem(BaseModel):
    axis_name: str
    label: str
    count: int
    pct: float


class AxisDistributionResponse(BaseModel):
    org_id: Optional[str]
    axis_name: str
    total_servers: int
    distribution: list[AxisDistributionItem]


class VerdictDistributionItem(BaseModel):
    verdict: str
    count: int
    pct: float


class VerdictDistributionResponse(BaseModel):
    org_id: Optional[str]
    total_servers: int
    distribution: list[VerdictDistributionItem]


class OverallRiskBucket(BaseModel):
    label: str  # LOW | MEDIUM | HIGH | CRITICAL
    count: int
    pct: float


class OverallRiskDistributionResponse(BaseModel):
    org_id: Optional[str]
    total_servers: int
    buckets: list[OverallRiskBucket]


class ServerVerdictSummary(BaseModel):
    server_id: str
    server_name: Optional[str]
    registry_source: Optional[str]
    verdict: Optional[str]
    risk_tier: Optional[str]
    last_assessed: Optional[str]


class TopServersResponse(BaseModel):
    org_id: Optional[str]
    axis_name: str
    label: str
    servers: list[ServerVerdictSummary]


class SummaryResponse(BaseModel):
    org_id: Optional[str]
    total_servers: int
    axis_count: int
    verdict_distribution: list[VerdictDistributionItem]
    overall_risk_buckets: list[OverallRiskBucket]
    generated_at: str


# --- Helpers ---------------------------------------------------------------

RISK_BUCKETS = [
    ("CRITICAL", lambda p_crit, p_dang: (p_crit or 0) >= 0.4 or (p_dang or 0) >= 0.6),
    ("HIGH",     lambda p_crit, p_dang: ((p_crit or 0) >= 0.15 or (p_dang or 0) >= 0.3) and not ((p_crit or 0) >= 0.4 or (p_dang or 0) >= 0.6)),
    ("MEDIUM",   lambda p_crit, p_dang: ((p_crit or 0) >= 0.05 or (p_dang or 0) >= 0.1) and not ((p_crit or 0) >= 0.15 or (p_dang or 0) >= 0.3)),
    ("LOW",      lambda p_crit, p_dang: True),
]


def _bucket_risk(p_critical: Optional[float], p_danger: Optional[float]) -> str:
    for label, fn in RISK_BUCKETS:
        if fn(p_critical, p_danger):
            return label
    return "LOW"


def _latest_model_version(db: Session) -> Optional[str]:
    row = (
        db.query(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
        .scalar()
    )
    return row


# --- Endpoints ------------------------------------------------------------

@router.get("/verdict/distribution", response_model=VerdictDistributionResponse)
def get_verdict_distribution(
    org_id: Optional[str] = Query(None, description="Scope to an org_id"),
    db: Session = Depends(get_session),
) -> VerdictDistributionResponse:
    """Return distribution of verdicts (trust_score bands) across servers.

    Verdict is derived from the server's risk_tier field in mcp_server_registry.
    """
    base_q = db.query(McpServerRegistry)
    if org_id:
        base_q = base_q.filter(McpServerRegistry.org_id == org_id)

    total = base_q.count()

    rows = (
        base_q
        .with_entities(
            McpServerRegistry.verdict,
            func.count(McpServerRegistry.id).label("cnt"),
        )
        .group_by(McpServerRegistry.verdict)
        .all()
    )

    distribution = [
        VerdictDistributionItem(
            verdict=r.verdict or "UNKNOWN",
            count=r.cnt,
            pct=round(r.cnt / total, 4) if total > 0 else 0.0,
        )
        for r in rows
    ]

    return VerdictDistributionResponse(
        org_id=org_id,
        total_servers=total,
        distribution=sorted(distribution, key=lambda x: -x.count),
    )


@router.get("/verdict/distribution/overall-risk", response_model=OverallRiskDistributionResponse)
def get_overall_risk_distribution(
    org_id: Optional[str] = Query(None, description="Scope to an org_id"),
    db: Session = Depends(get_session),
) -> OverallRiskDistributionResponse:
    """Return distribution of servers bucketed into CRITICAL/HIGH/MEDIUM/LOW
    based on the 'overall_risk' axis p_top / p_critical / p_danger probabilities.
    Uses the latest model_version only.
    """
    model_ver = _latest_model_version(db)

    base_q = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.axis_name == "overall_risk"
    )
    if org_id:
        base_q = base_q.join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        ).filter(McpServerRegistry.org_id == org_id)

    if model_ver:
        base_q = base_q.filter(McpLlmAxisScore.model_version == model_ver)

    total = base_q.count()

    rows = base_q.all()

    bucket_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        bucket = _bucket_risk(row.p_critical, row.p_danger)
        bucket_counts[bucket] += 1

    buckets = [
        OverallRiskBucket(
            label=label,
            count=bucket_counts.get(label, 0),
            pct=round(bucket_counts.get(label, 0) / total, 4) if total > 0 else 0.0,
        )
        for label in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    ]

    return OverallRiskDistributionResponse(
        org_id=org_id,
        total_servers=total,
        buckets=buckets,
    )


@router.get("/verdict/distribution/axis/{axis_name}", response_model=AxisDistributionResponse)
def get_axis_distribution(
    axis_name: str,
    org_id: Optional[str] = Query(None, description="Scope to an org_id"),
    db: Session = Depends(get_session),
) -> AxisDistributionResponse:
    """Return distribution of labels for a specific scoring axis (e.g. overall_risk,
    auth_strength, capability_breadth, data_sensitivity, network_egress,
    maintainer_trust, exploit_surface).
    """
    model_ver = _latest_model_version(db)

    base_q = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.axis_name == axis_name
    )
    if org_id:
        base_q = base_q.join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        ).filter(McpServerRegistry.org_id == org_id)

    if model_ver:
        base_q = base_q.filter(McpLlmAxisScore.model_version == model_ver)

    total = base_q.count()

    rows = (
        base_q
        .with_entities(
            McpLlmAxisScore.label,
            func.count(McpLlmAxisScore.id).label("cnt"),
        )
        .group_by(McpLlmAxisScore.label)
        .all()
    )

    distribution = [
        AxisDistributionItem(
            axis_name=axis_name,
            label=r.label or "UNKNOWN",
            count=r.cnt,
            pct=round(r.cnt / total, 4) if total > 0 else 0.0,
        )
        for r in rows
    ]

    return AxisDistributionResponse(
        org_id=org_id,
        axis_name=axis_name,
        total_servers=total,
        distribution=sorted(distribution, key=lambda x: -x.count),
    )


@router.get("/verdict/distribution/axis/{axis_name}/top-servers", response_model=TopServersResponse)
def get_top_servers_by_axis(
    axis_name: str,
    label: Optional[str] = Query(None, description="Filter by axis label value"),
    limit: int = Query(default=20, ge=1, le=200),
    org_id: Optional[str] = Query(None, description="Scope to an org_id"),
    db: Session = Depends(get_session),
) -> TopServersResponse:
    """Return servers with the highest p_top for the given axis, optionally filtered by label."""
    model_ver = _latest_model_version(db)

    base_q = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.axis_name == axis_name
    )
    if label:
        base_q = base_q.filter(McpLlmAxisScore.label == label)
    if org_id:
        base_q = base_q.join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        ).filter(McpServerRegistry.org_id == org_id)
    if model_ver:
        base_q = base_q.filter(McpLlmAxisScore.model_version == model_ver)

    rows = (
        base_q
        .order_by(McpLlmAxisScore.p_top.desc())
        .limit(limit)
        .all()
    )

    server_ids = [r.server_id for r in rows]
    servers = {
        s.server_id: s
        for s in db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    }

    servers_out = [
        ServerVerdictSummary(
            server_id=r.server_id,
            server_name=servers[r.server_id].name if r.server_id in servers else None,
            registry_source=servers[r.server_id].registry_source if r.server_id in servers else None,
            verdict=servers[r.server_id].verdict if r.server_id in servers else None,
            risk_tier=servers[r.server_id].risk_tier if r.server_id in servers else None,
            last_assessed=(
                servers[r.server_id].last_assessed.isoformat()
                if r.server_id in servers
                and servers[r.server_id].last_assessed
                else None
            ),
        )
        for r in rows
    ]

    return TopServersResponse(
        org_id=org_id,
        axis_name=axis_name,
        label=label or "ALL",
        servers=servers_out,
    )


@router.get("/verdict/distribution/summary", response_model=SummaryResponse)
def get_distribution_summary(
    org_id: Optional[str] = Query(None, description="Scope to an org_id"),
    db: Session = Depends(get_session),
) -> SummaryResponse:
    """Return a combined summary: verdict distribution + overall risk buckets + axis count."""
    # Verdict distribution
    verdict_resp = get_verdict_distribution(org_id=org_id, db=db)
    # Overall risk distribution
    risk_resp = get_overall_risk_distribution(org_id=org_id, db=db)

    # Axis count
    base_q = db.query(McpLlmAxisScore)
    if org_id:
        base_q = base_q.join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        ).filter(McpServerRegistry.org_id == org_id)
    axis_count = (
        db.query(func.count(func.distinct(McpLlmAxisScore.axis_name)))
        .filter(
            McpLlmAxisScore.axis_name.in_([
                "overall_risk", "auth_strength", "capability_breadth",
                "data_sensitivity", "network_egress", "maintainer_trust",
                "exploit_surface",
            ])
        )
        .scalar() or 0
    )

    return SummaryResponse(
        org_id=org_id,
        total_servers=verdict_resp.total_servers,
        axis_count=axis_count,
        verdict_distribution=verdict_resp.distribution,
        overall_risk_buckets=risk_resp.buckets,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# --- Self-test ------------------------------------------------------------

if __name__ == "__main__":
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
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    now = datetime.now(timezone.utc)
    model_ver = "test-v1"

    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="srv-001", name="Server A", org_id="org1",
                registry_source="npm", verdict="trusted", risk_tier="low",
            ),
            McpServerRegistry(
                server_id="srv-002", name="Server B", org_id="org1",
                registry_source="github", verdict="trusted", risk_tier="low",
            ),
            McpServerRegistry(
                server_id="srv-003", name="Server C", org_id="org1",
                registry_source="npm", verdict="untrusted", risk_tier="high",
            ),
            McpServerRegistry(
                server_id="srv-004", name="Server D", org_id="org2",
                registry_source="github", verdict="unknown", risk_tier="medium",
            ),
        ]
        db.add_all(servers)
        db.flush()

        axes_data = [
            # srv-001: HIGH overall
            ("srv-001", "overall_risk",     "HIGH",     0.3, 0.45, 0.7,  model_ver, now),
            ("srv-001", "auth_strength",     "MEDIUM",   0.6, 0.1,  0.15, model_ver, now),
            # srv-002: LOW overall
            ("srv-002", "overall_risk",     "LOW",      0.8, 0.05, 0.1,  model_ver, now),
            ("srv-002", "auth_strength",     "HIGH",    0.4, 0.2,  0.3,  model_ver, now),
            # srv-003: CRITICAL overall
            ("srv-003", "overall_risk",     "CRITICAL", 0.1, 0.7,  0.9,  model_ver, now),
            ("srv-003", "auth_strength",     "LOW",      0.3, 0.3,  0.4,  model_ver, now),
            # srv-004: MEDIUM overall
            ("srv-004", "overall_risk",     "MEDIUM",   0.5, 0.15, 0.25, model_ver, now),
            ("srv-004", "auth_strength",     "MEDIUM",   0.55,0.1,  0.2, model_ver, now),
        ]
        for sid, axis, label, p_top, p_crit, p_dang, mv, scored in axes_data:
            db.add(McpLlmAxisScore(
                server_id=sid, axis_name=axis, label=label,
                label_index={"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}.get(label, 0),
                p_top=p_top, p_critical=p_crit, p_danger=p_dang,
                model_version=mv, scored_at=scored,
            ))
        db.commit()

    client = TestClient(app)

    # --- Test verdict distribution (org1) ---
    resp1 = client.get("/api/verdict/distribution", params={"org_id": "org1"})
    assert resp1.status_code == 200, f"[verdict] {resp1.status_code}: {resp1.text}"
    d1 = resp1.json()
    assert d1["org_id"] == "org1"
    assert d1["total_servers"] == 3, d1["total_servers"]
    verdicts = {r["verdict"]: r["count"] for r in d1["distribution"]}
    assert verdicts.get("trusted") == 2, verdicts
    assert verdicts.get("untrusted") == 1, verdicts

    # --- Test overall risk distribution ---
    resp2 = client.get("/api/verdict/distribution/overall-risk", params={"org_id": "org1"})
    assert resp2.status_code == 200, f"[overall-risk] {resp2.status_code}: {resp2.text}"
    d2 = resp2.json()
    assert d2["total_servers"] == 3
    buckets = {b["label"]: b["count"] for b in d2["buckets"]}
    assert buckets.get("HIGH") == 1, buckets       # srv-001
    assert buckets.get("CRITICAL") == 1, buckets   # srv-003
    assert buckets.get("LOW") == 1, buckets       # srv-002

    # --- Test axis distribution ---
    resp3 = client.get("/api/verdict/distribution/axis/overall_risk")
    assert resp3.status_code == 200, f"[axis] {resp3.status_code}: {resp3.text}"
    d3 = resp3.json()
    assert d3["axis_name"] == "overall_risk"
    assert d3["total_servers"] == 4, d3["total_servers"]
    labels = {r["label"]: r["count"] for r in d3["distribution"]}
    assert labels.get("HIGH") == 1, labels
    assert labels.get("LOW") == 1, labels
    assert labels.get("CRITICAL") == 1, labels
    assert labels.get("MEDIUM") == 1, labels

    # --- Test axis distribution scoped to org1 ---
    resp4 = client.get("/api/verdict/distribution/axis/auth_strength", params={"org_id": "org1"})
    assert resp4.status_code == 200, f"[axis org1] {resp4.status_code}: {resp4.text}"
    d4 = resp4.json()
    assert d4["total_servers"] == 3, d4["total_servers"]

    # --- Test top servers by axis ---
    resp5 = client.get("/api/verdict/distribution/axis/overall_risk/top-servers", params={"limit": 3})
    assert resp5.status_code == 200, f"[top-servers] {resp5.status_code}: {resp5.text}"
    d5 = resp5.json()
    assert d5["label"] == "ALL"
    assert len(d5["servers"]) == 3, len(d5["servers"])
    # highest p_top -> srv-002 (LOW, 0.8) should be first
    assert d5["servers"][0]["server_id"] == "srv-002", d5["servers"][0]

    # --- Test top servers filtered by label ---
    resp6 = client.get("/api/verdict/distribution/axis/overall_risk/top-servers", params={"label": "HIGH", "limit": 5})
    assert resp6.status_code == 200
    d6 = resp6.json()
    assert d6["label"] == "HIGH"
    assert all(s["server_id"] == "srv-001" for s in d6["servers"]), d6["servers"]

    # --- Test summary ---
    resp7 = client.get("/api/verdict/distribution/summary", params={"org_id": "org1"})
    assert resp7.status_code == 200, f"[summary] {resp7.status_code}: {resp7.text}"
    d7 = resp7.json()
    assert d7["total_servers"] == 3, d7["total_servers"]
    assert len(d7["verdict_distribution"]) > 0
    assert len(d7["overall_risk_buckets"]) == 4

    # --- Test 404 for unknown axis ---
    resp8 = client.get("/api/verdict/distribution/axis/nonexistent_axis")
    # Should return 200 with empty distribution (axis not in DB)
    d8 = resp8.json()
    assert d8["total_servers"] == 0, d8["total_servers"]

    print("PASS")
    sys.exit(0)
