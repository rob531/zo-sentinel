"""HTTP surface for the axis p-critical bucket service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_p_critical_buckets as _get_p_critical_buckets
except ImportError:
    from .logic import p_critical_buckets as _get_p_critical_buckets

router = APIRouter(prefix="/api/axes", tags=["axis-p-critical-buckets"])


class BucketResponse(BaseModel):
    band: str
    count: int


class AxisBucketResponse(BaseModel):
    axis_name: str
    count: int
    escalated_count: int
    mean_p_critical: float


class CriticalBucketsResponse(BaseModel):
    total_rows: int
    buckets: list[BucketResponse]
    by_axis: list[AxisBucketResponse]
    escalated_total: int


def _bucket_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [{"band": str(band), "count": int(count)} for band, count in value.items()]
    rows: list[dict[str, Any]] = []
    for item in value or []:
        if isinstance(item, dict):
            rows.append({"band": str(item["band"]), "count": int(item["count"])})
        else:
            rows.append({"band": str(item[0]), "count": int(item[1])})
    return rows


def _axis_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value or []:
        if isinstance(item, dict):
            rows.append(
                {
                    "axis_name": str(item["axis_name"]),
                    "count": int(item["count"]),
                    "escalated_count": int(item["escalated_count"]),
                    "mean_p_critical": float(item["mean_p_critical"]),
                }
            )
        else:
            rows.append(
                {
                    "axis_name": str(item[0]),
                    "count": int(item[1]),
                    "escalated_count": int(item[2]),
                    "mean_p_critical": float(item[3]),
                }
            )
    return rows


def _response_from_logic(raw: Any) -> CriticalBucketsResponse:
    if isinstance(raw, dict):
        data = raw
    else:
        data = {
            "total_rows": raw[0],
            "buckets": raw[1],
            "by_axis": raw[2],
            "escalated_total": raw[3],
        }

    return CriticalBucketsResponse(
        total_rows=int(data["total_rows"]),
        buckets=_bucket_rows(data["buckets"]),
        by_axis=_axis_rows(data["by_axis"]),
        escalated_total=int(data["escalated_total"]),
    )


@router.get("/p-critical-buckets", response_model=CriticalBucketsResponse)
def get_p_critical_buckets(
    db: Session = Depends(get_session),
    axis_name: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
) -> CriticalBucketsResponse:
    raw = _get_p_critical_buckets(db, axis_name=axis_name, limit=limit)
    return _response_from_logic(raw)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    seed_values = [
        ("axis-a", 0.005, False),
        ("axis-a", 0.02, False),
        ("axis-a", 0.10, False),
        ("axis-b", 0.30, False),
        ("axis-b", 0.02, True),
        ("axis-b", 0.10, False),
    ]
    with TestSession() as session:
        for index, (axis, p_critical, escalated) in enumerate(seed_values, start=1):
            session.add(
                McpLlmAxisScore(
                    id=index,
                    server_id=f"test-server-{index}",
                    axis_name=axis,
                    model_version="test-model",
                    label="test-label",
                    label_index=0,
                    probs={},
                    p_top=p_critical,
                    p_critical=p_critical,
                    p_danger=p_critical,
                    escalated=escalated,
                    scored_at=datetime.now(timezone.utc),
                )
            )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/p-critical-buckets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 6
        assert sum(bucket["count"] for bucket in payload["buckets"]) == 6
        assert payload["escalated_total"] == 1

    print("PASS")