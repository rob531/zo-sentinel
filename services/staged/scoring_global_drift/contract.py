from __future__ import annotations

import sys
from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.db import Base
from app.models import McpLlmAxisScore, McpServerRegistry


class Metric(BaseModel):
    metric: str
    value: float


class GlobalDriftResponse(BaseModel):
    global_drift: float
    metrics: list[Metric]


router = APIRouter(prefix="/api")


@router.get("/scoring/drift", response_model=GlobalDriftResponse)
def get_global_drift(db: Session = Depends(get_session)) -> GlobalDriftResponse:
    global_value = db.query(func.avg(McpLlmAxisScore.p_danger)).scalar()
    axis_rows = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_danger).label("value"),
        )
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
        .all()
    )
    metrics = [Metric(metric="average_p_danger", value=float(global_value or 0.0))]
    if axis_rows:
        metrics = [
            Metric(metric=axis_name, value=float(value))
            for axis_name, value in axis_rows
        ]
    return GlobalDriftResponse(
        global_drift=float(global_value or 0.0),
        metrics=metrics,
    )


def run() -> bool:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSession() as db:
        for index in range(10):
            server_id = f"srv-{index + 1}"
            db.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=f"Server {index + 1}",
                    risk_tier="HIGH" if index % 2 else "LOW",
                )
            )
            db.add(
                McpLlmAxisScore(
                    id=index + 1,
                    server_id=server_id,
                    axis_name="test_axis",
                    model_version="model-1",
                    p_danger=index / 10.0,
                    p_critical=0.0,
                    p_top=0.0,
                    probs={},
                    scored_at=datetime(2024, 1, 1),
                )
            )
        db.commit()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/scoring/drift")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["metrics"]) == 1, payload
    assert payload["metrics"][0]["metric"] == "test_axis", payload
    assert abs(payload["metrics"][0]["value"] - 0.45) < 1e-6, payload
    assert abs(payload["global_drift"] - 0.45) < 1e-6, payload
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)
