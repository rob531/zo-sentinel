# deps: fastapi, sqlalchemy, pydantic
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_score_heatmap_logic"])

TIERS = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "INSUFFICIENT",
]


@router.get("/axis/heatmap")
def get_axis_heatmap(
    days: Annotated[int | None, Query(description="Filter scores by age in days")] = None,
    session: Session = Depends(get_session),
) -> dict:
    """
    Axis-score heatmap: per-axis breakdown of servers bucketed by risk tier.

    Returns a list of axes, each with a risk-tier bucket map
    {TIER: server_count}.  Each server is counted once per axis-tier combination.
    """
    query = (
        session.query(McpLlmAxisScore.axis_name, McpServerRegistry.risk_tier)
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
    )
    if days is not None and days > 0:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.filter(McpLlmAxisScore.scored_at >= cutoff)

    axis_counts: dict[str, dict[str, int]] = {}
    seen_servers: set[tuple[str, str]] = set()

    for axis_name, risk_tier in query.all():
        key = (axis_name, risk_tier)
        if key not in seen_servers:
            seen_servers.add(key)
            if axis_name not in axis_counts:
                axis_counts[axis_name] = {t: 0 for t in TIERS}
            if risk_tier in axis_counts[axis_name]:
                axis_counts[axis_name][risk_tier] += 1

    total_servers = sum(
        sum(buckets.values()) for buckets in axis_counts.values()
    )

    return {
        "axes": [
            {"axis_name": name, "buckets": buckets}
            for name, buckets in sorted(axis_counts.items())
        ],
        "days": days,
        "total_servers": total_servers,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    from app.models import Base
    Base.metadata.create_all(bind=_engine)
    _sm = sessionmaker(bind=_engine)
    _sess = _sm()

    _sess.add(McpServerRegistry(
        server_id="srv1", name="Server 1",
        risk_tier="TRUSTED_GENERAL", registry_source="test",
    ))
    _sess.add(McpServerRegistry(
        server_id="srv2", name="Server 2",
        risk_tier="TRUSTED_GENERAL", registry_source="test",
    ))
    _sess.add(McpServerRegistry(
        server_id="srv3", name="Server 3",
        risk_tier="CAUTION_LIMITED", registry_source="test",
    ))
    _sess.commit()

    _now = datetime.utcnow()
    for _srv, _ax in [("srv1", "security"), ("srv2", "security"), ("srv3", "privacy")]:
        _sess.add(McpLlmAxisScore(
            server_id=_srv, axis_name=_ax,
            scored_at=_now, model_version="v1",
            decision_rule_version="v1", adapter_sha256="test_sha256",
        ))
    _sess.commit()

    _app = FastAPI()
    _app.include_router(router)
    that_app = _app
    that_app.dependency_overrides[get_session] = lambda: _sess
    _client = TestClient(_app)

    _resp = _client.get("/api/axis/heatmap")
    if _resp.status_code != 200:
        print(f"FAIL: status {_resp.status_code}: {_resp.text}")
        exit(1)
    _data = _resp.json()

    if _data["total_servers"] != 3:
        print(f"FAIL: expected total_servers=3, got {_data['total_servers']}")
        exit(1)
    _axes = {ax["axis_name"]: ax["buckets"] for ax in _data["axes"]}
    if _axes["security"]["TRUSTED_GENERAL"] != 2:
        print(f"FAIL: security/TRUSTED_GENERAL expected 2, got {_axes['security']['TRUSTED_GENERAL']}")
        exit(1)
    if _axes["privacy"]["CAUTION_LIMITED"] != 1:
        print(f"FAIL: privacy/CAUTION_LIMITED expected 1, got {_axes['privacy']['CAUTION_LIMITED']}")
        exit(1)

    # days filter
    _resp2 = _client.get("/api/axis/heatmap?days=0")
    if _resp2.status_code != 200:
        print(f"FAIL: days=0 should return 200, got {_resp2.status_code}")
        exit(1)

    print("PASS")
