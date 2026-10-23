# services/staged/data_sensitivity_score/logic.py
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/scoring/data-sensitivity")


class DataSensitivityScoreResponse(BaseModel):
    server_id: int
    server_name: Optional[str]
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]
    scored_at: Optional[datetime]
    registry_verdict: Optional[str]


@router.get("/{server_id}", response_model=DataSensitivityScoreResponse)
def get_data_sensitivity_score(
    server_id: int, session: Session = Depends(get_session)
) -> DataSensitivityScoreResponse:
    # fetch the latest axis score for the given server and axis
    axis_score = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "data_sensitivity",
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    if not axis_score:
        raise HTTPException(status_code=404, detail="Axis score not found")

    # fetch server registry entry
    server_reg = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    server_name = server_reg.name if server_reg else None
    registry_verdict = server_reg.verdict if server_reg else None

    return DataSensitivityScoreResponse(
        server_id=server_id,
        server_name=server_name,
        axis_name=axis_score.axis_name,
        label=axis_score.label,
        label_index=axis_score.label_index,
        p_top=axis_score.p_top,
        p_critical=axis_score.p_critical,
        p_danger=axis_score.p_danger,
        escalated=axis_score.escalated,
        scored_at=axis_score.scored_at,
        registry_verdict=registry_verdict,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Minimal in‑memory stand‑in for the SQLAlchemy session
    # ------------------------------------------------------------------- #
    class _SimpleObj:
        """Container for attribute access."""

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _FakeQuery:
        def __init__(self, data):
            self._data = data

        def filter(self, *_, **__):
            return self

        def filter_by(self, **kwargs):
            # simple equality filter on stored dicts
            filtered = [
                obj
                for obj in self._data
                if all(getattr(obj, k) == v for k, v in kwargs.items())
            ]
            return _FakeQuery(filtered)

        def order_by(self, *_, **__):
            return self

        def first(self):
            return self._data[0] if self._data else None

    class _FakeSession:
        def __init__(self, axis_rows, server_rows):
            self._axis_rows = axis_rows
            self._server_rows = server_rows

        def query(self, model):
            if model is McpLlmAxisScore:
                return _FakeQuery(self._axis_rows)
            if model is McpServerRegistry:
                return _FakeQuery(self._server_rows)
            raise ValueError("Unsupported model in fake session")

    # ------------------------------------------------------------------- #
    # Seed known data
    # ------------------------------------------------------------------- #
    test_server_id = 42
    axis_row = _SimpleObj(
        server_id=test_server_id,
        axis_name="data_sensitivity",
        label="high",
        label_index=2,
        p_top=0.85,
        p_critical=0.10,
        p_danger=0.05,
        escalated=True,
        scored_at=datetime(2023, 1, 1, 12, 0, 0),
    )
    server_row = _SimpleObj(
        server_id=test_server_id,
        name="test-server",
        verdict="malicious",
    )

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def _override_get_session():
        return _FakeSession([axis_row], [server_row])

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    resp = client.get(f"/api/scoring/data-sensitivity/{test_server_id}")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert payload["label"] == "high", "Label does not match expected value"
    print("PASS")