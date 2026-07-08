from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_

# ----------------------------------------------------------------------
# Application data layer (must be imported exactly as required)
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    ServerRegistry,          # table with server_id, server_name, risk_tier, verdict
    LlmAxisScore,           # table with server_id, axis_name, p_top, p_critical, p_danger, label
    Base,                   # declarative base for test overrides
)

router = APIRouter()


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class ServerInfo(BaseModel):
    server_id: str
    server_name: str
    risk_tier: str
    verdict: str


class AxisScoreInfo(BaseModel):
    server_id: str
    p_top: float
    label: str
    p_critical: float
    p_danger: float


class AxisInfo(BaseModel):
    axis_name: str
    label: str
    scores: List[AxisScoreInfo]
    spread: float = Field(..., ge=0)


class OverallScoreInfo(BaseModel):
    server_id: str
    p_top: float
    label: str


class OverallComparison(BaseModel):
    axis_name: str = "overall_risk"
    scores: List[OverallScoreInfo]
    spread: float = Field(..., ge=0)


class ComparisonResponse(BaseModel):
    servers: List[ServerInfo]
    axes: List[AxisInfo]
    overall_comparison: OverallComparison


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def _fetch_servers(session: Session, ids: List[str]) -> List[ServerRegistry]:
    stmt = select(ServerRegistry).where(ServerRegistry.server_id.in_(ids))
    return session.execute(stmt).scalars().all()


def _fetch_latest_axis_scores(session: Session, ids: List[str]) -> List[LlmAxisScore]:
    """
    For each (server_id, axis_name) pair we want the most recent row.
    Assuming LlmAxisScore has a column `updated_at` (timestamp). If not,
    we simply take the first matching row.
    """
    subq = (
        select(
            LlmAxisScore.server_id,
            LlmAxisScore.axis_name,
            func.max(LlmAxisScore.updated_at).label("max_ts"),
        )
        .where(LlmAxisScore.server_id.in_(ids))
        .group_by(LlmAxisScore.server_id, LlmAxisScore.axis_name)
        .subquery()
    )
    stmt = (
        select(LlmAxisScore)
        .join(
            subq,
            and_(
                LlmAxisScore.server_id == subq.c.server_id,
                LlmAxisScore.axis_name == subq.c.axis_name,
                LlmAxisScore.updated_at == subq.c.max_ts,
            ),
        )
    )
    return session.execute(stmt).scalars().all()


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------
@router.get(
    "/servers/compare",
    response_model=ComparisonResponse,
    summary="Compare risk axes across multiple servers",
)
def compare_servers(
    server_ids: str = Query(
        ...,
        description="Comma‑separated list of server IDs (min 2, max 10)",
        min_length=1,
    ),
    session: Session = Depends(get_session),
):
    ids = [sid.strip() for sid in server_ids.split(",") if sid.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="At least two server IDs required")
    if len(ids) > 10:
        raise HTTPException(status_code=400, detail="Maximum ten server IDs allowed")

    # ------------------------------------------------------------------
    # Load server metadata
    # ------------------------------------------------------------------
    servers = _fetch_servers(session, ids)
    if len(servers) != len(set(ids)):
        raise HTTPException(status_code=404, detail="One or more server IDs not found")
    server_map = {s.server_id: s for s in servers}
    server_info_list = [
        ServerInfo(
            server_id=s.server_id,
            server_name=s.server_name,
            risk_tier=s.risk_tier,
            verdict=s.verdict,
        )
        for s in servers
    ]

    # ------------------------------------------------------------------
    # Load axis scores (latest per axis per server)
    # ------------------------------------------------------------------
    scores = _fetch_latest_axis_scores(session, ids)

    # Organise by axis
    axis_dict: Dict[str, List[LlmAxisScore]] = {}
    for sc in scores:
        axis_dict.setdefault(sc.axis_name, []).append(sc)

    axes_response: List[AxisInfo] = []
    overall_scores: List[OverallScoreInfo] = []

    for axis_name, sc_list in axis_dict.items():
        # Build score objects
        score_objs = [
            AxisScoreInfo(
                server_id=sc.server_id,
                p_top=sc.p_top,
                label=sc.label,
                p_critical=sc.p_critical,
                p_danger=sc.p_danger,
            )
            for sc in sc_list
        ]

        # Compute spread on p_top
        p_tops = [sc.p_top for sc in sc_list]
        spread_val = max(p_tops) - min(p_tops)

        # Use label from first entry (all share same label)
        label = sc_list[0].label if sc_list else ""

        axis_info = AxisInfo(
            axis_name=axis_name,
            label=label,
            scores=score_objs,
            spread=spread_val,
        )
        axes_response.append(axis_info)

        if axis_name == "overall_risk":
            overall_scores = [
                OverallScoreInfo(
                    server_id=sc.server_id,
                    p_top=sc.p_top,
                    label=sc.label,
                )
                for sc in sc_list
            ]

    # If overall_risk axis missing, create empty placeholder
    if not overall_scores:
        overall_scores = [
            OverallScoreInfo(server_id=sid, p_top=0.0, label="") for sid in ids
        ]

    overall_p_tops = [s.p_top for s in overall_scores]
    overall_spread = max(overall_p_tops) - min(overall_p_tops)

    overall_cmp = OverallComparison(
        scores=overall_scores,
        spread=overall_spread,
    )

    return ComparisonResponse(
        servers=server_info_list,
        axes=axes_response,
        overall_comparison=overall_cmp,
    )


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------
    # Create in‑memory SQLite and override get_session
    # ------------------------------------------------------------------
    engine = create_engine("sqlite:///:memory:", echo=False)
    TestingSessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:  # type: ignore
        return TestingSessionLocal()

    app = FastAPI()
    app.include_router(router)

    # Override dependency
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed data
    # ------------------------------------------------------------------
    with TestingSessionLocal() as db:
        # Servers
        srv1 = ServerRegistry(
            server_id="srv1",
            server_name="Server One",
            risk_tier="high",
            verdict="review",
        )
        srv2 = ServerRegistry(
            server_id="srv2",
            server_name="Server Two",
            risk_tier="medium",
            verdict="ok",
        )
        db.add_all([srv1, srv2])

        # Axes (7 axes including overall_risk)
        axes = [
            "confidentiality",
            "integrity",
            "availability",
            "privacy",
            "compliance",
            "performance",
            "overall_risk",
        ]
        for axis in axes:
            for srv, top in [(srv1, 0.2), (srv2, 0.4)]:
                db.add(
                    LlmAxisScore(
                        server_id=srv.server_id,
                        axis_name=axis,
                        p_top=top,
                        p_critical=0.1,
                        p_danger=0.05,
                        label=f"{axis}_label",
                        updated_at=func.now(),
                    )
                )
        db.commit()

    # ------------------------------------------------------------------
    # Test request
    # ------------------------------------------------------------------
    client = TestClient(app)
    resp = client.get("/servers/compare?server_ids=srv1,srv2")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # servers list matches query
    returned_ids = {s["server_id"] for s in data["servers"]}
    assert returned_ids == {"srv1", "srv2"}

    # axes list has 7 entries
    assert len(data["axes"]) == 7

    # each axis spread >= 0
    for axis in data["axes"]:
        assert axis["spread"] >= 0

    print("PASS")