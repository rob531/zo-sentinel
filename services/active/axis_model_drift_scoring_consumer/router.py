"""Axis model drift scoring consumer -- aggregates danger concentration per model version."""
# deps: fastapi, pydantic, sqlalchemy, httpx

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/scoring/consumers", tags=["axis_model_drift_scoring_consumer"])


class ModelDriftEntry(BaseModel):
    model_version: str
    adapter_sha256: str
    server_count: int
    danger_axis_count: int
    avg_p_danger_per_axis: float


class AxisModelDriftResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    models: list[ModelDriftEntry]


DANGER_THRESHOLD = 0.5


def _compute_drift(
    session: Session,
    period_start: datetime,
    period_end: datetime,
    threshold: float = DANGER_THRESHOLD,
) -> list[ModelDriftEntry]:
    rows = (
        session.query(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_danger,
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .filter(
            and_(
                McpLlmAxisScore.scored_at >= period_start,
                McpLlmAxisScore.scored_at <= period_end,
            )
        )
        .all()
    )

    # server_id -> (set of axes with p_danger > threshold, list of p_danger values)
    server_axes: dict[str, tuple[set[str], list[float]]] = {}
    for model_version, adapter_sha256, server_id, axis_name, p_danger in rows:
        srv_key = server_id
        if srv_key not in server_axes:
            server_axes[srv_key] = (set(), [])
        axes, pd_vals = server_axes[srv_key]
        if p_danger > threshold:
            axes.add(axis_name)
            pd_vals.append(p_danger)

    # group by (model_version, adapter_sha256)
    model_stats: dict[tuple, dict] = {}
    for server_id, (axes, pd_vals) in server_axes.items():
        if not axes:
            continue
        # find which model_version+adapter_sha256 this server belongs to
        matching = [(r[0], r[1]) for r in rows if r[2] == server_id]
        for model_version, adapter_sha256 in matching:
            mk = (model_version, adapter_sha256)
            if mk not in model_stats:
                model_stats[mk] = {
                    "server_count": 0,
                    "danger_axes": set(),
                    "total_p_danger": 0.0,
                    "axis_count": 0,
                }
            model_stats[mk]["server_count"] += 1
            model_stats[mk]["danger_axes"].update(axes)
            model_stats[mk]["total_p_danger"] += sum(pd_vals)
            model_stats[mk]["axis_count"] += len(pd_vals)

    results: list[ModelDriftEntry] = []
    for (model_version, adapter_sha256), stats in model_stats.items():
        avg_p = stats["total_p_danger"] / stats["axis_count"] if stats["axis_count"] else 0.0
        results.append(
            ModelDriftEntry(
                model_version=model_version,
                adapter_sha256=adapter_sha256,
                server_count=stats["server_count"],
                danger_axis_count=len(stats["danger_axes"]),
                avg_p_danger_per_axis=round(avg_p, 4),
            )
        )
    return results


@router.get("/axis-model-drift", response_model=AxisModelDriftResponse)
def get_axis_model_drift(
    period_start: Optional[datetime] = Query(
        default=None,
        description="Start of period (defaults to 2020-01-01 UTC)",
    ),
    period_end: Optional[datetime] = Query(
        default=None,
        description="End of period (defaults to now UTC)",
    ),
    session: Session = Depends(get_session),
) -> AxisModelDriftResponse:
    """Return cross-axis danger concentration per model version across the scoring period."""
    now = datetime.now(timezone.utc)
    start = period_start or datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = period_end or now
    models = _compute_drift(session, start, end)
    return AxisModelDriftResponse(period_start=start, period_end=end, models=models)


if __name__ == "__main__":
    import json
    import os
    import sys

    # Ensure project root is on sys.path so 'app' is importable
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from fastapi import FastAPI
    from sqlalchemy import Column, Float, String, DateTime, create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool

    TestBase = declarative_base()

    class _TestAxisScore(TestBase):
        __tablename__ = "mcp_llm_axis_scores"
        id = Column(String, primary_key=True)
        server_id = Column(String)
        adapter_sha256 = Column(String)
        model_version = Column(String)
        axis_name = Column(String)
        label = Column(String)
        label_index = Column(String)
        probs = Column(String)
        p_top = Column(Float)
        p_critical = Column(Float)
        p_danger = Column(Float)
        escalated = Column(String)
        escalated_to = Column(String)
        decision_rule_version = Column(String)
        scored_at = Column(DateTime)

    class _TestRegistry(TestBase):
        __tablename__ = "mcp_server_registry"
        server_id = Column(String, primary_key=True)
        name = Column(String)
        registry_source = Column(String)
        url = Column(String, default="")
        description = Column(String, default="")
        trust_score = Column(Float, default=0.5)
        verdict = Column(String, default="")
        verdict_reasoning = Column(String, default="")
        confidence = Column(Float, default=0.5)
        risk_tier = Column(String, default="medium")
        scan_count = Column(String, default="0")
        first_seen = Column(DateTime)
        last_seen = Column(DateTime)
        last_scanned = Column(DateTime)
        last_assessed = Column(DateTime)
        meta = Column(String, default="{}")

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestBase.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        ts = TestSession()
        try:
            yield ts
        finally:
            ts.close()

    ts = TestSession()
    t1 = datetime(2024, 6, 1, 12, 0, 0)

    srv1 = _TestRegistry(server_id="srv-001", name="Srv One", registry_source="test",
                          first_seen=t1, last_seen=t1, last_scanned=t1, last_assessed=t1)
    srv2 = _TestRegistry(server_id="srv-002", name="Srv Two", registry_source="test",
                          first_seen=t1, last_seen=t1, last_scanned=t1, last_assessed=t1)
    srv3 = _TestRegistry(server_id="srv-003", name="Srv Three", registry_source="test",
                          first_seen=t1, last_seen=t1, last_scanned=t1, last_assessed=t1)
    ts.add_all([srv1, srv2, srv3])

    axis_rows = [
        _TestAxisScore(id="ax-001", server_id="srv-001", adapter_sha256="f1",
                        model_version="v1.0", axis_name="safety", p_danger=0.75,
                        scored_at=t1, label="danger", label_index=2),
        _TestAxisScore(id="ax-002", server_id="srv-001", adapter_sha256="f1",
                        model_version="v1.0", axis_name="security", p_danger=0.65,
                        scored_at=t1, label="danger", label_index=2),
        _TestAxisScore(id="ax-003", server_id="srv-002", adapter_sha256="f1",
                        model_version="v1.0", axis_name="safety", p_danger=0.80,
                        scored_at=t1, label="danger", label_index=2),
        _TestAxisScore(id="ax-004", server_id="srv-002", adapter_sha256="f1",
                        model_version="v1.0", axis_name="compliance", p_danger=0.70,
                        scored_at=t1, label="danger", label_index=2),
        _TestAxisScore(id="ax-005", server_id="srv-003", adapter_sha256="f2",
                        model_version="v2.0", axis_name="safety", p_danger=0.85,
                        scored_at=t1, label="danger", label_index=2),
        _TestAxisScore(id="ax-006", server_id="srv-003", adapter_sha256="f2",
                        model_version="v2.0", axis_name="security", p_danger=0.72,
                        scored_at=t1, label="danger", label_index=2),
        _TestAxisScore(id="ax-007", server_id="srv-001", adapter_sha256="f1",
                        model_version="v1.0", axis_name="robustness", p_danger=0.10,
                        scored_at=t1, label="safe", label_index=0),
    ]
    ts.add_all(axis_rows)
    ts.commit()
    ts.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        resp = client.get(
            "/api/scoring/consumers/axis-model-drift",
            params={"period_start": "2024-01-01T00:00:00", "period_end": "2024-12-31T23:59:59"},
        )
        if resp.status_code != 200:
            print(f"FAIL: HTTP {resp.status_code}")
            sys.exit(1)
        data = resp.json()
        model_count = len(data.get("models", []))
        if model_count != 2:
            print(f"FAIL: expected 2 models, got {model_count}: {json.dumps(data, default=str)}")
            sys.exit(1)
        for m in data["models"]:
            assert "model_version" in m
            assert "adapter_sha256" in m
            assert "server_count" in m
            assert "danger_axis_count" in m
            assert "avg_p_danger_per_axis" in m
        print("PASS")
