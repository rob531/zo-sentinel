# services/staged/data_sensitivity_score/contract.py
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class DataSensitivityScoreResponse(BaseModel):
    server_id: int
    server_name: str
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: datetime
    registry_verdict: Optional[str] = None


@router.get(
    "/scoring/data-sensitivity/{server_id}",
    response_model=DataSensitivityScoreResponse,
)
def get_data_sensitivity_score(
    server_id: int,
    session=Depends(get_session),
) -> DataSensitivityScoreResponse:
    axis = (
        session.query(McpLlmAxisScore)
        .filter_by(server_id=server_id, axis_name="data_sensitivity")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    if not axis:
        raise HTTPException(status_code=404, detail="Axis score not found")

    registry = (
        session.query(McpServerRegistry)
        .filter_by(server_id=server_id)
        .first()
    )
    if not registry:
        raise HTTPException(status_code=404, detail="Server registry entry not found")

    return DataSensitivityScoreResponse(
        server_id=axis.server_id,
        server_name=registry.name,
        axis_name=axis.axis_name,
        label=axis.label,
        label_index=axis.label_index,
        p_top=axis.p_top,
        p_critical=axis.p_critical,
        p_danger=axis.p_danger,
        escalated=axis.escalated,
        scored_at=axis.scored_at,
        registry_verdict=registry.verdict,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ----- Mock models ------------------------------------------------------ #
    class _MockMcpLlmAxisScore:
        def __init__(
            self,
            server_id: int,
            axis_name: str,
            label: str,
            label_index: int,
            p_top: float,
            p_critical: float,
            p_danger: float,
            escalated: bool,
            scored_at: datetime,
        ):
            self.server_id = server_id
            self.axis_name = axis_name
            self.label = label
            self.label_index = label_index
            self.p_top = p_top
            self.p_critical = p_critical
            self.p_danger = p_danger
            self.escalated = escalated
            self.scored_at = scored_at

    class _MockMcpServerRegistry:
        def __init__(self, server_id: int, name: str, verdict: str):
            self.server_id = server_id
            self.name = name
            self.verdict = verdict

    # ----- Mock query ------------------------------------------------------- #
    class _MockQuery:
        def __init__(self, data):
            self._data = data

        def filter_by(self, **kwargs):
            filtered = [
                obj
                for obj in self._data
                if all(getattr(obj, k) == v for k, v in kwargs.items())
            ]
            return _MockQuery(filtered)

        def order_by(self, *args, **kwargs):
            # ordering not needed for the static test data
            return self

        def first(self):
            return self._data[0] if self._data else None

    # ----- Mock session ------------------------------------------------------ #
    class _MockSession:
        def __init__(self, axis_rows, registry_rows):
            self._axis_rows = axis_rows
            self._registry_rows = registry_rows

        def query(self, model):
            if model is McpLlmAxisScore:
                return _MockQuery(self._axis_rows)
            if model is McpServerRegistry:
                return _MockQuery(self._registry_rows)
            return _MockQuery([])

    # ----- Seed data -------------------------------------------------------- #
    test_server_id = 42
    mock_axis = _MockMcpLlmAxisScore(
        server_id=test_server_id,
        axis_name="data_sensitivity",
        label="high",
        label_index=2,
        p_top=0.1,
        p_critical=0.2,
        p_danger=0.7,
        escalated=True,
        scored_at=datetime(2023, 1, 1, 12, 0, 0),
    )
    mock_registry = _MockMcpServerRegistry(
        server_id=test_server_id,
        name="test-server",
        verdict="malicious",
    )
    mock_session = _MockSession([mock_axis], [mock_registry])

    # ----- FastAPI app with dependency override ----------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: mock_session

    client = TestClient(app)
    resp = client.get(f"/api/scoring/data-sensitivity/{test_server_id}")

    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    json = resp.json()
    assert json["label"] == mock_axis.label, "Label mismatch"
    print("PASS")