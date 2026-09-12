"""
Student Adapter Registry Service - Thin API Router
Manages SFT student model adapters for risk scoring.
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["adapters"])


class AdapterResponse(BaseModel):
    model_version: str
    adapter_sha256: str
    decision_rule_version: str
    axes_covered: List[str]
    server_count: int
    latest_scored_at: datetime


class AdaptersListResponse(BaseModel):
    adapters: List[AdapterResponse]


@router.get("/scoring/adapters", response_model=AdaptersListResponse)
def get_scoring_adapters(db: Session = Depends(get_session)) -> AdaptersListResponse:
    """
    Returns registry of active adapters keyed by model_version + adapter_sha256,
    with per-axis label coverage counts.
    """
    # Query all axis scores and group by model_version + adapter_sha256
    query = (
        db.query(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
            func.count(distinct(McpLlmAxisScore.axis_name)).label("axis_count"),
            func.count(distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
        )
        .subquery()
    )

    # Get distinct axes per adapter
    axes_query = (
        db.query(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            func.string_agg(distinct(McpLlmAxisScore.axis_name), ",").label("axes_list"),
        )
        .group_by(McpLlmAxisScore.model_version, McpLlmAxisScore.adapter_sha256)
    )

    axes_map = {
        (row.model_version, row.adapter_sha256): row.axes_list.split(",")
        for row in axes_query.all()
    }

    adapters = []
    for row in db.execute(query).fetchall():
        key = (row.model_version, row.adapter_sha256)
        adapters.append(
            AdapterResponse(
                model_version=row.model_version,
                adapter_sha256=row.adapter_sha256,
                decision_rule_version=row.decision_rule_version,
                axes_covered=axes_map.get(key, []),
                server_count=row.server_count,
                latest_scored_at=row.latest_scored_at,
            )
        )

    return AdaptersListResponse(adapters=adapters)


if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from main import app

    # Create in-memory test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    # Seed test data: 3 axis-score rows with 2 different model_versions
    session = TestSessionLocal()
    from datetime import timezone

    now = datetime.now(timezone.utc)

    # Adapter 1: model_v1
    session.add(
        McpLlmAxisScore(
            id=1,
            model_version="model_v1",
            adapter_sha256="sha256_abc123",
            decision_rule_version="rule_v1",
            axis_name="security",
            server_id="srv_001",
            label="high",
            scored_at=now,
        )
    )
    session.add(
        McpLlmAxisScore(
            id=2,
            model_version="model_v1",
            adapter_sha256="sha256_abc123",
            decision_rule_version="rule_v1",
            axis_name="reliability",
            server_id="srv_001",
            label="medium",
            scored_at=now,
        )
    )
    # Adapter 2: model_v2
    session.add(
        McpLlmAxisScore(
            id=3,
            model_version="model_v2",
            adapter_sha256="sha256_def456",
            decision_rule_version="rule_v2",
            axis_name="performance",
            server_id="srv_002",
            label="low",
            scored_at=now,
        )
    )
    session.commit()
    session.close()

    # Override dependency
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/scoring/adapters")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    adapter_list = data.get("adapters", [])

    # Assert adapter count = 2
    assert len(adapter_list) == 2, f"Expected 2 adapters, got {len(adapter_list)}"

    # Verify axis coverage for each adapter
    adapter_map = {a["model_version"]: a for a in adapter_list}

    # Check model_v1 has 2 axes
    if "model_v1" in adapter_map:
        v1_axes = adapter_map["model_v1"]["axes_covered"]
        assert len(v1_axes) == 2, f"Expected 2 axes for model_v1, got {len(v1_axes)}"
        assert "security" in v1_axes
        assert "reliability" in v1_axes

    # Check model_v2 has 1 axis
    if "model_v2" in adapter_map:
        v2_axes = adapter_map["model_v2"]["axes_covered"]
        assert len(v2_axes) == 1, f"Expected 1 axis for model_v2, got {len(v2_axes)}"
        assert "performance" in v2_axes

    app.dependency_overrides.clear()
    print("PASS")