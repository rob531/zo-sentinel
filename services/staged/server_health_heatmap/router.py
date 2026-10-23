from __future__ import annotations

if not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = "services.staged.server_health_heatmap"

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import HeatmapResponse, _compute_heatmap

router = APIRouter(prefix="/api", tags=["server_health_heatmap"])


@router.get("/heatmap/axis", response_model=HeatmapResponse)
def get_heatmap_axis(
    period_days: int = Query(default=7, ge=1),
    db: Session = Depends(get_session),
) -> HeatmapResponse:
    return _compute_heatmap(db, period_days)


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpLlmAxisScore, McpServerRegistry

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session():
        with TestSessionLocal() as session:
            yield session

    now = datetime.utcnow()
    servers = [
        McpServerRegistry(server_id="srv-1", name="Server 1", risk_tier="low"),
        McpServerRegistry(server_id="srv-2", name="Server 2", risk_tier="medium"),
        McpServerRegistry(server_id="srv-3", name="Server 3", risk_tier="high"),
        McpServerRegistry(server_id="srv-4", name="Server 4", risk_tier="critical"),
        McpServerRegistry(server_id="srv-5", name="Server 5", risk_tier="medium"),
    ]
    with TestSessionLocal() as session:
        session.add_all(servers)
        session.add_all(
            [
                McpLlmAxisScore(
                    id=index,
                    server_id=f"srv-{server_number}",
                    axis_name=axis_name,
                    label=axis_label,
                    model_version="self-test",
                    scored_at=now,
                )
                for index, (axis_name, axis_label, server_number) in enumerate(
                    [
                        ("availability", "Availability", number)
                        for number in range(1, 6)
                    ]
                    + [
                        ("performance", "Performance", number)
                        for number in range(1, 6)
                    ],
                    start=1,
                )
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    response = TestClient(test_app).get("/api/heatmap/axis?period_days=7")
    assert response.status_code == 200, (
        f"Unexpected status {response.status_code}: {response.text}"
    )
    payload = response.json()
    assert payload["period_days"] == 7
    assert payload["rows"], "Rows should not be empty"
    print("PASS")