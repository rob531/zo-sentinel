from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import insert, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class TrustScoreBucket(BaseModel):
    label: str
    count: int
    min: float
    max: float


class TrustScoreHistogramResponse(BaseModel):
    total: int
    bucketed: int
    null_count: int
    buckets: List[TrustScoreBucket]


router = APIRouter(prefix="/api", tags=["registry"])


def calculate_trust_score_histogram(session: Session) -> Dict[str, Any]:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_assessed,
        )
    ).all()

    buckets = [
        {
            "label": f"{index * 10}-{(index + 1) * 10}",
            "count": 0,
            "min": float(index * 10),
            "max": float((index + 1) * 10),
        }
        for index in range(10)
    ]

    null_count = 0

    for row in rows:
        score = row.trust_score
        if score is None:
            null_count += 1
            continue

        score = float(score)
        if score < 0:
            bucket_index = 0
        elif score >= 100:
            bucket_index = 9
        else:
            bucket_index = int(score // 10)

        bucket = buckets[bucket_index]
        bucket["count"] += 1
        if bucket["count"] == 1:
            bucket["min"] = score
            bucket["max"] = score
        else:
            bucket["min"] = min(bucket["min"], score)
            bucket["max"] = max(bucket["max"], score)

    buckets.append(
        {
            "label": "NULL",
            "count": null_count,
            "min": 0.0,
            "max": 0.0,
        }
    )

    total = len(rows)
    return {
        "total": total,
        "bucketed": total - null_count,
        "null_count": null_count,
        "buckets": buckets,
    }


@router.get(
    "/registry/trust-score-histogram",
    response_model=TrustScoreHistogramResponse,
)
def get_trust_score_histogram(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return calculate_trust_score_histogram(session)


def _self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    test_session_factory = sessionmaker(bind=test_engine)

    scores = [0.0, 55.0, 95.0, None] + [0.0] * 21
    rows = [
        {
            "server_id": f"histogram-server-{index}",
            "trust_score": score,
        }
        for index, score in enumerate(scores)
    ]

    with test_session_factory() as session:
        session.execute(insert(McpServerRegistry.__table__), rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/trust-score-histogram")
        assert response.status_code == 200

        payload = response.json()
        assert payload["total"] == 25
        assert payload["null_count"] == 1
        assert payload["bucketed"] == 24
        assert any(
            bucket["label"] == "50-60" and bucket["count"] == 1
            for bucket in payload["buckets"]
        )
        assert any(
            bucket["label"] == "NULL" and bucket["count"] == 1
            for bucket in payload["buckets"]
        )

    print("PASS")


if __name__ == "__main__":
    _self_test()