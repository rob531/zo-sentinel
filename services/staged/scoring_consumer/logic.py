# services/staged/scoring_consumer/logic.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional

# Real data layer imports (must remain unchanged)
from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()


def _extract_axis_name(row: Any) -> str:
    """Return the axis identifier from a model row, handling possible column names."""
    for attr in ("axis", "axis_name", "name"):
        val = getattr(row, attr, None)
        if val is not None:
            return str(val)
    raise AttributeError("Axis name not found on McpLlmAxisScore row.")


def _extract_label(row: Any) -> str:
    """Return the risk label from a model row, handling possible column names."""
    for attr in ("label", "risk_label", "risk"):
        val = getattr(row, attr, None)
        if val is not None:
            return str(val)
    return ""


def _extract_p_top(row: Any) -> Optional[float]:
    """Return the p_top probability from a model row."""
    return getattr(row, "p_top", None)


def _extract_overall(row: Any) -> Optional[float]:
    """Return the overall risk score from a model row."""
    return getattr(row, "overall_risk", None)


def _extract_criteria_version(row: Any) -> Optional[str]:
    """Return the criteria version from a model row."""
    return getattr(row, "criteria_version", None)


def fetch_axis_scores(server_id: int, db: Session) -> List[McpLlmAxisScore]:
    """Retrieve all axis score rows for a given server."""
    return (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )


def compute_risk_tier(overall: float, axes: Dict[str, Dict[str, Any]]) -> str:
    """Determine the risk tier, applying the CRITICAL override rule."""
    # Override: any axis labelled CRITICAL forces the tier to CRITICAL
    for axis_data in axes.values():
        if axis_data.get("label") == "CRITICAL":
            return "CRITICAL"

    # Default tiering based on overall risk score
    if overall >= 0.8:
        return "HIGH"
    if overall >= 0.5:
        return "MEDIUM"
    return "LOW"


def build_scoring_response(server_id: int, db: Session) -> Dict[str, Any]:
    """Construct the response payload for the scoring consumer endpoint."""
    rows = fetch_axis_scores(server_id, db)
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    axes: Dict[str, Dict[str, Any]] = {}
    overall: Optional[float] = None
    criteria_version: Optional[str] = None

    for row in rows:
        axis_name = _extract_axis_name(row)
        label = _extract_label(row)
        p_top = _extract_p_top(row)

        axes[axis_name] = {"label": label, "p_top": p_top}

        # Capture overall risk and criteria version from the first row that provides them
        if overall is None:
            overall = _extract_overall(row)
        if criteria_version is None:
            criteria_version = _extract_criteria_version(row)

    # Fallbacks if the model does not store overall/criteria on each row
    overall = overall if overall is not None else 0.0
    criteria_version = criteria_version if criteria_version is not None else ""

    risk_tier = compute_risk_tier(overall, axes)

    return {
        "axes": axes,
        "overall": overall,
        "risk_tier": risk_tier,
        "criteria_version": criteria_version,
    }


@router.get("/scoring/consumer")
def scoring_consumer(
    server_id: int = Query(..., description="Identifier of the server"),
    db: Session = Depends(get_session),
):
    """FastAPI endpoint exposing the scoring consumer data."""
    return build_scoring_response(server_id, db)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ----------------------------------------------------------------------- #
    # Minimal in‑memory test harness – overrides the real DB dependency with
    # a dummy session that returns fabricated rows matching the expected schema.
    # ----------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Dummy row object mimicking the real model
    class DummyRow:
        def __init__(
            self,
            server_id: int,
            axis: str,
            label: str,
            p_top: float,
            overall_risk: float,
            criteria_version: str,
        ):
            self.server_id = server_id
            self.axis = axis
            self.label = label
            self.p_top = p_top
            self.overall_risk = overall_risk
            self.criteria_version = criteria_version

    # Dummy query / session that works with the above DummyRow
    class DummyQuery:
        def __init__(self, data: List[DummyRow]):
            self._data = data

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return self._data

    class DummySession:
        def __init__(self, data: List[DummyRow]):
            self._data = data

        def query(self, model):
            # The model argument is ignored – we always return the dummy data
            return DummyQuery(self._data)

    # Prepare fabricated data: 6 axes + overall risk (overall stored on each row)
    test_rows = [
        DummyRow(
            server_id=1,
            axis="confidentiality",
            label="CRITICAL",
            p_top=0.95,
            overall_risk=0.85,
            criteria_version="v1",
        ),
        DummyRow(
            server_id=1,
            axis="integrity",
            label="MEDIUM",
            p_top=0.80,
            overall_risk=0.85,
            criteria_version="v1",
        ),
        DummyRow(
            server_id=1,
            axis="availability",
            label="MEDIUM",
            p_top=0.75,
            overall_risk=0.85,
            criteria_version="v1",
        ),
        DummyRow(
            server_id=1,
            axis="privacy",
            label="MEDIUM",
            p_top=0.70,
            overall_risk=0.85,
            criteria_version="v1",
        ),
        DummyRow(
            server_id=1,
            axis="authenticity",
            label="MEDIUM",
            p_top=0.65,
            overall_risk=0.85,
            criteria_version="v1",
        ),
        DummyRow(
            server_id=1,
            axis="nonrepudiation",
            label="MEDIUM",
            p_top=0.60,
            overall_risk=0.85,
            criteria_version="v1",
        ),
    ]

    # FastAPI app for the test
    app = FastAPI()
    app.include_router(router)

    # Dependency override to inject the dummy session
    def get_test_session() -> Session:
        return DummySession(test_rows)  # type: ignore

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/scoring/consumer?server_id=1")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()

    # Verify payload structure
    assert isinstance(payload, dict), "Response is not a dict"
    assert "axes" in payload, "Missing 'axes' key"
    assert "overall" in payload, "Missing 'overall' key"
    assert "risk_tier" in payload, "Missing 'risk_tier' key"
    assert "criteria_version" in payload, "Missing 'criteria_version' key"

    # Expect exactly 6 axes
    assert len(payload["axes"]) == 6, f"Expected 6 axes, got {len(payload['axes'])}"
    # Overall risk should match the fabricated value
    assert abs(payload["overall"] - 0.85) < 1e-6, "Overall risk mismatch"
    # CRITICAL override should force tier to CRITICAL
    assert payload["risk_tier"] == "CRITICAL", f"Risk tier mismatch: {payload['risk_tier']}"

    print("PASS")