from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import AxisScoreDeltaResponse, get_axis_score_delta

router = APIRouter(prefix="/api", tags=["axis_score_delta_consumer"])


@router.get("/scoring/delta", response_model=AxisScoreDeltaResponse)
def axis_score_delta(
    server_id: int = Query(..., description="Target server identifier"),
    db: Session = Depends(get_session),
) -> AxisScoreDeltaResponse:
    return get_axis_score_delta(server_id, db)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpLlmAxisScore

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime(2025, 1, 2, 12, 0, 0)
    earlier = now - timedelta(hours=1)

    with TestSessionLocal() as db:
        score_id = 1
        for server_id in ("1", "2", "3"):
            for axis_name in ("confidentiality", "integrity"):
                if server_id == "1" and axis_name == "confidentiality":
                    values = (0.8, 0.6)
                elif server_id == "1" and axis_name == "integrity":
                    values = (0.4, 0.5)
                else:
                    values = (0.5, 0.5)

                for event_index, (scored_at, p_top) in enumerate(
                    ((earlier, values[0]), (now, values[1]))
                ):
                    db.add(
                        McpLlmAxisScore(
                            id=score_id,
                            server_id=server_id,
                            axis_name=axis_name,
                            model_version=f"contract-{event_index}",
                            scored_at=scored_at,
                            p_top=p_top,
                        )
                    )
                    score_id += 1
        db.commit()

    def get_test_session():
        with TestSessionLocal() as db:
            yield db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session
    response = TestClient(test_app).get(
        "/api/scoring/delta", params={"server_id": 1}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["server_id"] == 1
    degraded_axis = next(
        axis for axis in payload["axes"] if axis["axis_name"] == "confidentiality"
    )
    assert degraded_axis["delta"] < 0
    print("PASS")