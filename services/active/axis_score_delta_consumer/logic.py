from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Union

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class AxisDelta(BaseModel):
    axis_name: str
    p_top_current: float
    p_top_previous: float
    delta: float
    direction: str


class AxisScoreDeltaResponse(BaseModel):
    server_id: Union[int, str]
    axes: List[AxisDelta]


def get_axis_score_delta(
    server_id: Union[int, str], db: Session = Depends(get_session)
) -> AxisScoreDeltaResponse:
    server_key = str(server_id)
    axis_names = db.execute(
        select(McpLlmAxisScore.axis_name)
        .where(
            McpLlmAxisScore.server_id == server_key,
            McpLlmAxisScore.scored_at.is_not(None),
        )
        .distinct()
        .order_by(McpLlmAxisScore.axis_name)
    ).scalars().all()

    axes: List[AxisDelta] = []
    for axis_name in axis_names:
        scores = db.execute(
            select(McpLlmAxisScore)
            .where(
                McpLlmAxisScore.server_id == server_key,
                McpLlmAxisScore.axis_name == axis_name,
                McpLlmAxisScore.scored_at.is_not(None),
            )
            .order_by(
                McpLlmAxisScore.scored_at.desc(),
                McpLlmAxisScore.id.desc(),
            )
            .limit(2)
        ).scalars().all()

        if len(scores) < 2:
            continue

        current, previous = scores
        if current.p_top is None or previous.p_top is None:
            continue

        p_top_current = float(current.p_top)
        p_top_previous = float(previous.p_top)
        delta = p_top_current - p_top_previous
        direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
        axes.append(
            AxisDelta(
                axis_name=axis_name,
                p_top_current=p_top_current,
                p_top_previous=p_top_previous,
                delta=delta,
                direction=direction,
            )
        )

    return AxisScoreDeltaResponse(server_id=server_id, axes=axes)


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from services.staged.axis_score_delta_consumer.router import router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    earlier = datetime(2025, 1, 2, 11, 0, 0)
    later = datetime(2025, 1, 2, 12, 0, 0)
    values = {
        "1": {"confidentiality": (0.8, 0.6), "integrity": (0.4, 0.5)},
        "2": {"confidentiality": (0.5, 0.5), "integrity": (0.3, 0.3)},
        "3": {"confidentiality": (0.2, 0.4), "integrity": (0.7, 0.6)},
    }

    with TestSessionLocal() as db:
        score_id = 1
        for target_server_id, server_axes in values.items():
            for axis_name, (previous_score, current_score) in server_axes.items():
                for event_index, (scored_at, p_top) in enumerate(
                    ((earlier, previous_score), (later, current_score))
                ):
                    db.add(
                        McpLlmAxisScore(
                            id=score_id,
                            server_id=target_server_id,
                            axis_name=axis_name,
                            model_version=f"self-test-{event_index}",
                            scored_at=scored_at,
                            p_top=p_top,
                        )
                    )
                    score_id += 1
        db.commit()

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session
    response = TestClient(test_app).get(
        "/api/scoring/delta", params={"server_id": "1"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["server_id"] == 1, payload
    assert len(payload["axes"]) == 2, payload
    degraded_axis = next(
        axis for axis in payload["axes"] if axis["axis_name"] == "confidentiality"
    )
    assert degraded_axis["delta"] < 0, payload
    print("PASS")