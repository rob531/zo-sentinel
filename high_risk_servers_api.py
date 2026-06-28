#!/usr/bin/env python3
"""
high_risk_servers_api.py

Read‑only FastAPI router exposing elevated‑risk servers (published tier only).
Mirrors the structure and self‑test of ``verdict_breakdown_api.py``.
"""

from __future__ import annotations

from typing import List, Optional, Dict

from fastapi import APIRouter, Depends, FastAPI, Query, HTTPException
from sqlalchemy import select, desc, and_, func
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Application imports – must match the existing project layout
# --------------------------------------------------------------------------- #
from app.db import get_session, Base
from app.models import McpServerRegistry, McpLlmAxisScore
from trust_gating_override import trust_gate

# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api", tags=["high_risk"])


@router.get("/high-risk-servers")
def list_high_risk_servers(
    tier: Optional[str] = Query(
        None, description="Risk tier to filter on (HIGH or CRITICAL)"
    ),
    source: Optional[str] = Query(
        None, description="Registry source to filter on"
    ),
    limit: int = Query(
        100, ge=1, le=100, description="Maximum number of servers to return (1‑100)"
    ),
    offset: int = Query(
        0, ge=0, description="Offset for pagination"
    ),
    db: Session = Depends(get_session),
) -> Dict:
    """
    Return servers whose *published* overall risk is HIGH or CRITICAL.
    The published risk is obtained via ``trust_gate`` which applies the
    official‑maintainer trust gating.
    """
    # ------------------------------------------------------------------- #
    # Base query – apply source filter, order by name
    # ------------------------------------------------------------------- #
    stmt = select(McpServerRegistry)

    if source:
        stmt = stmt.where(McpServerRegistry.registry_source == source)

    stmt = stmt.order_by(McpServerRegistry.name).offset(offset).limit(limit)

    servers: List[McpServerRegistry] = db.execute(stmt).scalars().all()

    result_servers: List[Dict] = []

    for srv in servers:
        # ----------------------------------------------------------------
        # Pull the latest scores for the two axes we care about
        # ----------------------------------------------------------------
        scores_stmt = (
            select(McpLlmAxisScore)
            .where(
                and_(
                    McpLlmAxisScore.server_id == srv.server_id,
                    McpLlmAxisScore.axis_name.in_(["overall_risk", "maintainer_trust"]),
                )
            )
            .order_by(desc(McpLlmAxisScore.scored_at))
        )
        scores = db.execute(scores_stmt).scalars().all()

        latest: Dict[str, str] = {}
        for sc in scores:
            if sc.axis_name not in latest:
                latest[sc.axis_name] = sc.label
            if len(latest) == 2:
                break

        # Need at least overall_risk to decide inclusion
        overall_label = latest.get("overall_risk")
        if not overall_label:
            continue

        maintainer_label = latest.get("maintainer_trust", "")

        # ----------------------------------------------------------------
        # Apply trust gating
        # ----------------------------------------------------------------
        gated = trust_gate(
            srv.url,
            srv.name,
            {"overall_risk": overall_label, "maintainer_trust": maintainer_label},
        )
        published_overall_risk = gated.get("published_overall_risk")
        trusted = gated.get("trusted", False)

        # ----------------------------------------------------------------
        # Filter by tier and by published risk level
        # ----------------------------------------------------------------
        allowed = {"HIGH", "CRITICAL"}
        if published_overall_risk not in allowed:
            continue
        if tier and published_overall_risk != tier.upper():
            continue

        result_servers.append(
            {
                "server_id": srv.server_id,
                "name": srv.name,
                "url": srv.url,
                "registry_source": srv.registry_source,
                "published_overall_risk": published_overall_risk,
                "trusted": trusted,
            }
        )

    return {
        "servers": result_servers,
        "count": len(result_servers),
        "offset": offset,
        "limit": limit,
    }


# --------------------------------------------------------------------------- #
# Self‑test (mirrors the exemplar in ``verdict_breakdown_api.py``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    import traceback
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (Postgres‑compatible schema via Base metadata)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # ------------------------------------------------------------------- #
    # Seed data – two servers with overall_risk scores
    # ------------------------------------------------------------------- #
    srv1 = McpServerRegistry(
        server_id="comm1",
        name="Community Knockoff",
        url="https://github.com/randomuser/knockoff-mcp",
        registry_source="test",
    )
    srv2 = McpServerRegistry(
        server_id="off1",
        name="Microsoft MCP",
        url="https://github.com/microsoft/mcp",
        registry_source="test",
    )
    session.add_all([srv1, srv2])
    session.flush()  # obtain PKs if needed

    # overall_risk scores
    score1 = McpLlmAxisScore(
        id=1,
        server_id="comm1",
        axis_name="overall_risk",
        label="CRITICAL",
        model_version="v3.0_40974559",
    )
    score2 = McpLlmAxisScore(
        id=2,
        server_id="off1",
        axis_name="overall_risk",
        label="HIGH",
        model_version="v3.0_40974559",
    )
    # maintainer_trust scores (required by trust_gate)
    score3 = McpLlmAxisScore(
        id=3,
        server_id="comm1",
        axis_name="maintainer_trust",
        label="HIGH",
        model_version="v3.0_40974559",
    )
    score4 = McpLlmAxisScore(
        id=4,
        server_id="off1",
        axis_name="maintainer_trust",
        label="HIGH",
        model_version="v3.0_40974559",
    )
    session.add_all([score1, score2, score3, score4])
    session.commit()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring with dependency overrides
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the DB dependency to use our in‑memory session
    def get_test_session() -> Session:
        return session

    app.dependency_overrides[get_session] = get_test_session

    # If the project uses auth/principal deps, stub them out as admin.
    # (No such deps are imported here, so we skip this step.)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    try:
        resp = client.get("/api/high-risk-servers")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        returned_ids = {s["server_id"] for s in data.get("servers", [])}
        assert "comm1" in returned_ids, "comm1 missing from result"
        assert "off1" not in returned_ids, "off1 should have been filtered out"
        print("PASS")
    except Exception as exc:
        print(f"FAIL: {exc}")
        traceback.print_exc()
        sys.exit(1)

    # ------------------------------------------------------------------- #
    # Verify the file compiles cleanly
    # ------------------------------------------------------------------- #
    import py_compile

    try:
        py_compile.compile("high_risk_servers_api.py", doraise=True)
    except py_compile.PyCompileError as ce:
        print(f"FAIL: compilation error – {ce}")
        sys.exit(1)