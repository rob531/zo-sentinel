import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Data layer (must come from the real app)
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
CURRENT_CRITERIA_VERSION = "v1.0"
AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class AxisInfo(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    scored_at: Optional[datetime.datetime] = None
    decision_rule_version: Optional[str] = None
    is_stale_gt_30d: bool = False
    is_missing: bool = True


class CriteriaAuditResponse(BaseModel):
    server_id: int
    registry_verdict: Optional[str] = None
    registry_risk_tier: Optional[str] = None
    registry_last_scanned: Optional[datetime.datetime] = None
    axes: List[AxisInfo]
    overall_stale_count: int
    missing_count: int
    criteria_version_current: str = CURRENT_CRITERIA_VERSION
    advice: str


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------
router = APIRouter()


@router.get(
    "/servers/{server_id}/criteria-audit",
    response_model=CriteriaAuditResponse,
    name="criteria_audit",
)
def criteria_audit(
    server_id: int, session: Session = Depends(get_session)
) -> CriteriaAuditResponse:
    # ------------------------------------------------------------------
    # Fetch server registry entry
    # ------------------------------------------------------------------
    registry = (
        session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    )
    if not registry:
        raise HTTPException(status_code=404, detail="Server not found")

    # ------------------------------------------------------------------
    # Build per‑axis health information
    # ------------------------------------------------------------------
    axes_info: List[AxisInfo] = []
    stale_cnt = 0
    missing_cnt = 0
    now = datetime.datetime.utcnow()

    for axis in AXES:
        score = (
            session.query(McpLlmAxisScores)
            .filter(
                McpLlmAxisScores.server_id == server_id,
                McpLlmAxisScores.axis_name == axis,
            )
            .order_by(McpLlmAxisScores.scored_at.desc())
            .first()
        )
        if not score:
            missing_cnt += 1
            axes_info.append(
                AxisInfo(
                    axis_name=axis,
                    is_missing=True,
                    is_stale_gt_30d=False,
                )
            )
            continue

        is_stale = (now - score.scored_at) > datetime.timedelta(days=30)
        if is_stale:
            stale_cnt += 1

        axes_info.append(
            AxisInfo(
                axis_name=axis,
                label=getattr(score, "label", None),
                p_top=getattr(score, "p_top", None),
                scored_at=score.scored_at,
                decision_rule_version=getattr(score, "decision_rule_version", None),
                is_stale_gt_30d=is_stale,
                is_missing=False,
            )
        )

    # ------------------------------------------------------------------
    # Advice generation
    # ------------------------------------------------------------------
    advice_parts = []
    if missing_cnt:
        advice_parts.append(f"{missing_cnt} axis(es) missing scores")
    if stale_cnt:
        advice_parts.append(f"{stale_cnt} axis(es) stale >30d")
    if not advice_parts:
        advice = "All risk criteria are up‑to‑date."
    else:
        advice = "; ".join(advice_parts) + "."

    return CriteriaAuditResponse(
        server_id=server_id,
        registry_verdict=getattr(registry, "verdict", None),
        registry_risk_tier=getattr(registry, "risk_tier", None),
        registry_last_scanned=getattr(registry, "last_scanned", None),
        axes=axes_info,
        overall_stale_count=stale_cnt,
        missing_count=missing_cnt,
        criteria_version_current=CURRENT_CRITERIA_VERSION,
        advice=advice,
    )


# ----------------------------------------------------------------------
# Self‑test (executed when run as script)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB that mirrors the real models
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Import the Base from the app models to create tables
    from app.models import Base  # type: ignore

    Base.metadata.create_all(bind=engine)

    # Helper to provide a session dependency that uses the in‑memory DB
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data
    with SessionLocal() as db:
        # Server with complete, fresh scores
        db.add(
            McpServerRegistry(
                server_id=1,
                last_scanned=datetime.datetime.utcnow() - datetime.timedelta(days=1),
                risk_tier="high",
                verdict="accept",
            )
        )
        for axis in AXES:
            db.add(
                McpLlmAxisScores(
                    server_id=1,
                    axis_name=axis,
                    label=f"{axis} label",
                    p_top=0.9,
                    scored_at=datetime.datetime.utcnow() - datetime.timedelta(days=5),
                    decision_rule_version=CURRENT_CRITERIA_VERSION,
                )
            )
        # Server with missing axes
        db.add(
            McpServerRegistry(
                server_id=2,
                last_scanned=datetime.datetime.utcnow() - datetime.timedelta(days=2),
                risk_tier="medium",
                verdict="review",
            )
        )
        for axis in AXES[:3]:  # only three axes scored
            db.add(
                McpLlmAxisScores(
                    server_id=2,
                    axis_name=axis,
                    label=f"{axis} label",
                    p_top=0.7,
                    scored_at=datetime.datetime.utcnow() - datetime.timedelta(days=40),
                    decision_rule_version=CURRENT_CRITERIA_VERSION,
                )
            )
        db.commit()

    # Build FastAPI app and override dependency
    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ---- Test 1: all axes fresh ----
    resp = client.get("/servers/1/criteria-audit")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["missing_count"] == 0, "Expected no missing axes"
    assert data["overall_stale_count"] == 0, "Expected no stale axes"
    for axis in data["axes"]:
        assert axis["is_missing"] is False
        assert axis["is_stale_gt_30d"] is False

    # ---- Test 2: missing / stale axes ----
    resp = client.get("/servers/2/criteria-audit")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["missing_count"] == len(AXES) - 3, "Missing count mismatch"
    assert data["overall_stale_count"] == 3, "Stale count mismatch"
    for axis in data["axes"]:
        if axis["is_missing"]:
            continue
        assert axis["is_stale_gt_30d"] is True

    # ---- Test 3: server not found ----
    resp = client.get("/servers/999/criteria-audit")
    assert resp.status_code == 404, "Expected 404 for unknown server"

    print("PASS")