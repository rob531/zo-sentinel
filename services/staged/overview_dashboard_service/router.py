from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import OverviewDashboardResponse, get_overview_data

router = APIRouter(prefix="/api", tags=["overview_dashboard_service"])


@router.get("/dashboard/overview", response_model=OverviewDashboardResponse)
def overview_dashboard(
    session: Session = Depends(get_session),
) -> OverviewDashboardResponse:
    return get_overview_data(session)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autoflush=False, autocommit=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="Server One",
            risk_tier="low",
            confidence=0.9,
            description="",
            first_seen=now,
            last_assessed=now,
            last_scanned=now,
            last_seen=now,
            meta=None,
            registry_source="self-test",
            scan_count=1,
            trust_score=0.8,
            url="https://example.com/1",
            verdict="clean",
            verdict_reasoning="",
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="Server Two",
            risk_tier="medium",
            confidence=0.7,
            description="",
            first_seen=now,
            last_assessed=now,
            last_scanned=now,
            last_seen=now,
            meta=None,
            registry_source="self-test",
            scan_count=1,
            trust_score=0.6,
            url="https://example.com/2",
            verdict="clean",
            verdict_reasoning="",
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="Server Three",
            risk_tier="high",
            confidence=0.5,
            description="",
            first_seen=now,
            last_assessed=now,
            last_scanned=now,
            last_seen=now,
            meta=None,
            registry_source="self-test",
            scan_count=1,
            trust_score=0.4,
            url="https://example.com/3",
            verdict="risky",
            verdict_reasoning="",
        ),
    ]
    axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]
    scores = [
        McpLlmAxisScore(
            id=index,
            server_id="srv-1",
            axis_name=axis,
            p_top=0.1 * index,
            p_critical=0.0,
            p_danger=0.0,
            adapter_sha256="",
            decision_rule_version="self-test",
            escalated=False,
            escalated_to=None,
            label="",
            label_index=0,
            model_version="self-test",
            probs={},
            scored_at=now,
        )
        for index, axis in enumerate(axes, start=1)
    ]

    with TestingSessionLocal() as db:
        db.add_all(servers)
        db.add_all(scores)
        db.commit()

    response = TestClient(that_app).get("/api/dashboard/overview")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["total_servers"] == 3, "total_servers mismatch"
    assert sum(data["tier_counts"].values()) == data["total_servers"], (
        "tier_counts do not sum to total_servers"
    )
    returned_axes = {item["axis_name"] for item in data["axis_averages"]}
    assert set(axes) == returned_axes, "axis_averages missing or extra axes"
    print("PASS")