# deps: fastapi, pydantic, sqlalchemy
"""Server Risk Comparison API.

Compares risk profiles of multiple MCP servers side-by-side.
Public endpoint -- no authentication required.

GET /api/servers/compare?server_ids=srv-1,srv-2
  Returns per-server axis scores (7 axes) and risk_tier for comparison.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_risk_comparison_api"])


class AxisScore(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None


class ServerComparison(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    axes: List[AxisScore]
    overall_risk_label: Optional[str] = None
    overall_risk_p_top: Optional[float] = None


class ComparisonResponse(BaseModel):
    servers: List[ServerComparison]


AXIS_NAMES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


@router.get("/servers/compare", response_model=ComparisonResponse)
def compare_servers(
    server_ids: str = Query(..., description="Comma-separated server IDs"),
    db: Session = Depends(get_session),
) -> ComparisonResponse:
    """
    Compare risk profiles of multiple servers side-by-side.
    Returns all 7 axis scores per server.
    """
    id_list = [s.strip() for s in server_ids.split(",") if s.strip()]
    if not id_list:
        return ComparisonResponse(servers=[])

    servers_out: List[ServerComparison] = []

    for sid in id_list:
        server = db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == sid
        ).first()

        if not server:
            continue

        axes_raw = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == sid
        ).all()

        axes_map = {a.axis_name: a for a in axes_raw}

        overall_risk_label: Optional[str] = None
        overall_risk_p_top: Optional[float] = None

        axes: List[AxisScore] = []
        for ax_name in AXIS_NAMES:
            score = axes_map.get(ax_name)
            if score:
                axes.append(AxisScore(
                    axis_name=ax_name,
                    label=score.label,
                    p_top=score.p_top,
                    p_critical=score.p_critical,
                    p_danger=score.p_danger,
                ))
                if ax_name == "overall_risk":
                    overall_risk_label = score.label
                    overall_risk_p_top = score.p_top
            else:
                axes.append(AxisScore(axis_name=ax_name))

        servers_out.append(ServerComparison(
            server_id=sid,
            name=server.name,
            risk_tier=server.risk_tier,
            axes=axes,
            overall_risk_label=overall_risk_label,
            overall_risk_p_top=overall_risk_p_top,
        ))

    return ComparisonResponse(servers=servers_out)


if __name__ == "__main__":
    import sys
    from datetime import datetime
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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(test_engine)

    def _override_get_session():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.utcnow()
    with TestSession() as sess:
        sess.add(McpServerRegistry(server_id="cmp-001", name="Alpha Server", risk_tier="LOW"))
        sess.add(McpServerRegistry(server_id="cmp-002", name="Beta Server", risk_tier="HIGH"))
        sess.add(McpServerRegistry(server_id="cmp-003", name="Gamma Server", risk_tier="CRITICAL"))

        axes_data = {
            "cmp-001": [
                ("overall_risk", "LOW", 0.1, 0.2, 0.7),
                ("auth_strength", "STRONG", 0.9, 0.05, 0.05),
                ("capability_breadth", "NARROW", 0.2, 0.3, 0.5),
                ("data_sensitivity", "LOW", 0.15, 0.25, 0.6),
                ("network_egress", "RESTRICTED", 0.1, 0.2, 0.7),
                ("maintainer_trust", "TRUSTED", 0.8, 0.1, 0.1),
                ("exploit_surface", "MINIMAL", 0.05, 0.1, 0.85),
            ],
            "cmp-002": [
                ("overall_risk", "HIGH", 0.75, 0.15, 0.1),
                ("auth_strength", "WEAK", 0.2, 0.4, 0.4),
                ("capability_breadth", "WIDE", 0.7, 0.2, 0.1),
                ("data_sensitivity", "HIGH", 0.8, 0.15, 0.05),
                ("network_egress", "OPEN", 0.85, 0.1, 0.05),
                ("maintainer_trust", "UNKNOWN", 0.3, 0.4, 0.3),
                ("exploit_surface", "LARGE", 0.8, 0.15, 0.05),
            ],
        }

        for sid, axes in axes_data.items():
            for axis_name, label, p_top, p_critical, p_danger in axes:
                sess.add(McpLlmAxisScore(
                    server_id=sid,
                    axis_name=axis_name,
                    label=label,
                    p_top=p_top,
                    p_critical=p_critical,
                    p_danger=p_danger,
                    model_version="test-v1",
                    scored_at=now,
                ))
        sess.commit()

    client = TestClient(test_app)

    # Test 1: compare two servers
    resp = client.get("/api/servers/compare?server_ids=cmp-001,cmp-002")
    if resp.status_code != 200:
        print(f"FAIL: compare endpoint returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if "servers" not in data:
        print("FAIL: missing 'servers' key")
        sys.exit(1)
    servers = data["servers"]
    if len(servers) != 2:
        print(f"FAIL: expected 2 servers, got {len(servers)}")
        sys.exit(1)

    ids_found = {s["server_id"] for s in servers}
    if "cmp-001" not in ids_found or "cmp-002" not in ids_found:
        print(f"FAIL: expected cmp-001 and cmp-002, got {ids_found}")
        sys.exit(1)

    for s in servers:
        if len(s["axes"]) != 7:
            print(f"FAIL: expected 7 axes for {s['server_id']}, got {len(s['axes'])}")
            sys.exit(1)
        axis_names = {a["axis_name"] for a in s["axes"]}
        for expected in AXIS_NAMES:
            if expected not in axis_names:
                print(f"FAIL: missing axis {expected} in {s['server_id']}")
                sys.exit(1)

    # Test 2: cmp-001 overall_risk should be LOW
    alpha = next(s for s in servers if s["server_id"] == "cmp-001")
    if alpha["overall_risk_label"] != "LOW":
        print(f"FAIL: expected cmp-001 overall_risk_label=LOW, got {alpha['overall_risk_label']}")
        sys.exit(1)
    if alpha["risk_tier"] != "LOW":
        print(f"FAIL: expected cmp-001 risk_tier=LOW, got {alpha['risk_tier']}")
        sys.exit(1)

    # Test 3: cmp-002 overall_risk should be HIGH
    beta = next(s for s in servers if s["server_id"] == "cmp-002")
    if beta["overall_risk_label"] != "HIGH":
        print(f"FAIL: expected cmp-002 overall_risk_label=HIGH, got {beta['overall_risk_label']}")
        sys.exit(1)

    # Test 4: single server
    resp2 = client.get("/api/servers/compare?server_ids=cmp-003")
    if resp2.status_code != 200:
        print(f"FAIL: single server returned {resp2.status_code}")
        sys.exit(1)
    if len(resp2.json()["servers"]) != 1:
        print("FAIL: expected 1 server for cmp-003")
        sys.exit(1)

    # Test 5: unknown server returns empty list (skipped)
    resp3 = client.get("/api/servers/compare?server_ids=unknown-srv")
    if resp3.status_code != 200:
        print(f"FAIL: unknown server returned {resp3.status_code}")
        sys.exit(1)
    if len(resp3.json()["servers"]) != 0:
        print("FAIL: unknown server should return empty servers list")
        sys.exit(1)

    # Test 6: empty input
    resp4 = client.get("/api/servers/compare?server_ids=")
    if resp4.status_code != 200:
        print(f"FAIL: empty server_ids returned {resp4.status_code}")
        sys.exit(1)
    if len(resp4.json()["servers"]) != 0:
        print("FAIL: empty server_ids should return empty servers list")
        sys.exit(1)

    print("PASS")
