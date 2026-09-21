"""
services/staged/server_risk_detail/contract.py

FastAPI contract for the *server_risk_detail* service.

Provides:
    GET /api/servers/{server_id}/risk
"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Data layer – must be the real application models / session
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import McpLlmAxisScore, Base  # type: ignore

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class AxisDetail(BaseModel):
    label: str = Field(..., description="Human readable label for the axis")
    p_top: float = Field(..., description="Probability that the axis is at its top risk")


class ServerRiskDetail(BaseModel):
    axes: Dict[str, AxisDetail] = Field(..., description="Mapping of axis name to its detail")
    overall: float = Field(..., description="Overall risk score")
    risk_tier: str = Field(..., description="Risk tier after applying overrides")
    criteria_version: str = Field(..., description="Version of the criteria used for scoring")


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/risk",
    response_model=ServerRiskDetail,
    name="server_risk_detail",
)
def get_server_risk_detail(
    server_id: int,
    db=Depends(get_session),
) -> ServerRiskDetail:
    """
    Retrieve risk information for a given server.

    The underlying table `McpLlmAxisScore` contains one row per risk axis
    (including an overall row).  The response aggregates those rows into the
    required structure and applies a rule‑override: if any axis has the label
    ``CRITICAL`` the resulting ``risk_tier`` is forced to ``CRITICAL``.
    """
    rows = db.query(McpLlmAxisScore).filter_by(server_id=server_id).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    axes: Dict[str, AxisDetail] = {}
    overall: float | None = None
    risk_tier: str | None = None
    criteria_version: str | None = None
    critical_override = False

    for row in rows:
        # Capture common fields (they are the same across rows)
        if risk_tier is None:
            risk_tier = getattr(row, "risk_tier", "")
        if criteria_version is None:
            criteria_version = getattr(row, "criteria_version", "")

        # Overall row is identified by axis == "overall"
        axis_name = getattr(row, "axis", "")
        if axis_name == "overall":
            overall = getattr(row, "overall_risk", None)
            continue

        # Normal axis rows
        label = getattr(row, "label", "")
        p_top = getattr(row, "p_top", 0.0)
        axes[axis_name] = AxisDetail(label=label, p_top=p_top)

        if label.upper() == "CRITICAL":
            critical_override = True

    if overall is None:
        raise HTTPException(status_code=500, detail="Overall risk missing")

    # Apply rule‑override
    final_tier = "CRITICAL" if critical_override else (risk_tier or "UNKNOWN")

    return ServerRiskDetail(
        axes=axes,
        overall=overall,
        risk_tier=final_tier,
        criteria_version=criteria_version or "unknown",
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.server_risk_detail.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build a temporary in‑memory SQLite DB that mirrors the real models
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    # Create tables defined in app.models
    Base.metadata.create_all(bind=engine)

    # Seed test data
    with SessionLocal() as db:
        # Six normal axes
        axes_data = [
            {"axis": "confidentiality", "label": "LOW", "p_top": 0.1},
            {"axis": "integrity", "label": "MEDIUM", "p_top": 0.3},
            {"axis": "availability", "label": "HIGH", "p_top": 0.6},
            {"axis": "authenticity", "label": "LOW", "p_top": 0.2},
            {"axis": "nonrepudiation", "label": "MEDIUM", "p_top": 0.4},
            # This axis triggers the override
            {"axis": "privacy", "label": "CRITICAL", "p_top": 0.9},
        ]
        for a in axes_data:
            db.add(
                McpLlmAxisScore(
                    server_id=1,
                    axis=a["axis"],
                    label=a["label"],
                    p_top=a["p_top"],
                    overall_risk=None,
                    risk_tier="MEDIUM",
                    criteria_version="v1",
                )
            )
        # Overall row
        db.add(
            McpLlmAxisScore(
                server_id=1,
                axis="overall",
                label="",
                p_top=0.0,
                overall_risk=0.55,
                risk_tier="MEDIUM",
                criteria_version="v1",
            )
        )
        db.commit()

    # ------------------------------------------------------------------- #
    # Override the dependency to use the temporary session
    # ------------------------------------------------------------------- #
    def get_test_session():
        with SessionLocal() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/servers/1/risk")
    if resp.status_code != 200:
        print(f"FAIL – unexpected status {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    # Expect six axes, overall risk, and the overridden tier
    if (
        isinstance(data.get("axes"), dict)
        and len(data["axes"]) == 6
        and isinstance(data.get("overall"), (int, float))
        and data.get("risk_tier") == "CRITICAL"
    ):
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL – unexpected payload")
        sys.exit(1)