from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from datetime import datetime

from app.db import get_session
from app.models import McpLlmAxisScore
from services.staged.axis_score_heatmap.router import router

RISK_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

BUCKET_RANGES = [
    ("0-20", 0.0, 0.2),
    ("20-40", 0.2, 0.4),
    ("40-60", 0.4, 0.6),
    ("60-80", 0.6, 0.8),
    ("80-100", 0.8, 1.0),
]


class AxisHeatmapBucket(BaseModel):
    range: str
    counts: list[int]


class AxisHeatmapResponse(BaseModel):
    axes: list[str]
    buckets: list[AxisHeatmapBucket]
    total_servers: int


def get_axis_heatmap_query(limit: int, session: Session) -> AxisHeatmapResponse:
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.axis_name.in_(RISK_AXES))
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit)
        .all()
    )

    unique_servers = set()
    bucket_counts = {range_str: [0] * len(RISK_AXES) for range_str, _, _ in BUCKET_RANGES}

    for score in scores:
        unique_servers.add(score.server_id)
        p = score.p_top
        if p is None:
            p = 0.0
        for range_str, low, high in BUCKET_RANGES:
            if low <= p < high:
                idx = RISK_AXES.index(score.axis_name)
                bucket_counts[range_str][idx] += 1
                break

    buckets = [
        AxisHeatmapBucket(range=r, counts=c)
        for r, c in bucket_counts.items()
    ]

    return AxisHeatmapResponse(
        axes=RISK_AXES,
        buckets=buckets,
        total_servers=len(unique_servers),
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)

    now = datetime.utcnow()
    session.add(McpLlmAxisScore(id=1, server_id=1, adapter_sha256='abc123', axis_name='overall_risk', p_top=0.95, label='critical', label_index=3, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=2, server_id=1, adapter_sha256='abc123', axis_name='auth_strength', p_top=0.95, label='critical', label_index=3, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=3, server_id=1, adapter_sha256='abc123', axis_name='capability_breadth', p_top=0.95, label='critical', label_index=3, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=4, server_id=2, adapter_sha256='def456', axis_name='data_sensitivity', p_top=0.95, label='critical', label_index=3, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=5, server_id=2, adapter_sha256='def456', axis_name='network_egress', p_top=0.55, label='high', label_index=2, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=6, server_id=2, adapter_sha256='def456', axis_name='maintainer_trust', p_top=0.55, label='high', label_index=2, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=7, server_id=3, adapter_sha256='ghi789', axis_name='exploit_surface', p_top=0.55, label='high', label_index=2, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=8, server_id=3, adapter_sha256='ghi789', axis_name='overall_risk', p_top=0.15, label='low', label_index=0, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=9, server_id=3, adapter_sha256='ghi789', axis_name='auth_strength', p_top=0.85, label='critical', label_index=3, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.add(McpLlmAxisScore(id=10, server_id=3, adapter_sha256='ghi789', axis_name='capability_breadth', p_top=0.15, label='low', label_index=0, probs='{}', p_critical=0.0, p_danger=0.0, model_version='v1', decision_rule_version='v1', scored_at=now, escalated=False, escalated_to=None))
    session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: session

    client = TestClient(that_app)
    response = client.get("/api/axis/heatmap")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["axes"]) == 7, f"Expected 7 axes, got {len(data['axes'])}"
    assert len(data["buckets"]) == 5, f"Expected 5 buckets, got {len(data['buckets'])}"

    print("PASS")