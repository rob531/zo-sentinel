"""logic.py -- p-critical band distribution data and computation layer."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore


def _clamp_bucket_size(bucket_size: float) -> float:
    try:
        value = float(bucket_size)
    except (TypeError, ValueError):
        value = 0.1
    if not math.isfinite(value):
        value = 0.1
    return max(0.01, min(value, 0.25))


def get_critical_band_distribution(
    db: Session,
    axis_name: Optional[str] = None,
    bucket_size: float = 0.1,
) -> Dict[str, Any]:
    """Return p-critical distribution grouped by axis and probability band."""
    width = _clamp_bucket_size(bucket_size)
    band_index = cast(
        func.floor(McpLlmAxisScore.p_critical / width),
        Integer,
    )

    query = select(
        McpLlmAxisScore.axis_name,
        band_index.label("band_index"),
        func.count().label("count"),
        func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        func.count(McpLlmAxisScore.server_id.distinct()).label(
            "servers_distinct"
        ),
    ).where(McpLlmAxisScore.p_critical.is_not(None))

    if axis_name is not None:
        query = query.where(McpLlmAxisScore.axis_name == axis_name)

    rows = db.execute(
        query.group_by(McpLlmAxisScore.axis_name, band_index).order_by(
            McpLlmAxisScore.axis_name,
            band_index,
        )
    ).all()

    distinct_query = select(
        McpLlmAxisScore.axis_name,
        func.count(McpLlmAxisScore.server_id.distinct()).label(
            "servers_distinct"
        ),
    ).where(McpLlmAxisScore.p_critical.is_not(None))

    if axis_name is not None:
        distinct_query = distinct_query.where(
            McpLlmAxisScore.axis_name == axis_name
        )

    distinct_rows = db.execute(
        distinct_query.group_by(McpLlmAxisScore.axis_name)
    ).all()
    distinct_by_axis = {
        row.axis_name: int(row.servers_distinct)
        for row in distinct_rows
    }

    bands_by_axis: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        index = int(row.band_index)
        avg_p_top = row.avg_p_top
        bands_by_axis.setdefault(row.axis_name, []).append(
            {
                "band_index": index,
                "band_low": index * width,
                "band_high": (index + 1) * width,
                "count": int(row.count),
                "avg_p_top": (
                    float(avg_p_top) if avg_p_top is not None else None
                ),
            }
        )

    axes = [
        {
            "axis_name": name,
            "bands": bands,
            "total_count": distinct_by_axis.get(name, 0),
        }
        for name, bands in bands_by_axis.items()
    ]

    grand_total = sum(
        int(row.count)
        for row in rows
    )

    return {
        "bucket_size": width,
        "axes": axes,
        "grand_total": grand_total,
    }


def compute_critical_band_distribution(
    db: Session,
    axis_name: Optional[str] = None,
    bucket_size: float = 0.1,
) -> Dict[str, Any]:
    return get_critical_band_distribution(db, axis_name, bucket_size)


def get_p_critical_band_distribution(
    db: Session,
    axis_name: Optional[str] = None,
    bucket_size: float = 0.1,
) -> Dict[str, Any]:
    return get_critical_band_distribution(db, axis_name, bucket_size)


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as db:
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id=1,
                    axis_name="reliability",
                    model_version="test-model",
                    p_critical=0.05,
                    p_top=0.10,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id=2,
                    axis_name="reliability",
                    model_version="test-model",
                    p_critical=0.15,
                    p_top=0.20,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id=3,
                    axis_name="reliability",
                    model_version="test-model",
                    p_critical=0.95,
                    p_top=0.90,
                ),
            ]
        )
        db.commit()

    local_app = FastAPI()

    def override_get_session():
        with session_factory() as db:
            yield db

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/scoring/p-critical-bands")
    def critical_bands(
        axis: Optional[str] = None,
        bucket_size: float = 0.1,
        db: Session = Depends(get_session),
    ):
        return get_critical_band_distribution(db, axis, bucket_size)

    with TestClient(local_app) as client:
        response = client.get(
            "/api/scoring/p-critical-bands",
            params={"axis": "reliability", "bucket_size": 0.1},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["grand_total"] == 3
        assert len(payload["axes"]) == 1
        assert len(payload["axes"][0]["bands"]) == 3
        assert [band["count"] for band in payload["axes"][0]["bands"]] == [
            1,
            1,
            1,
        ]
        for band in payload["axes"][0]["bands"]:
            assert band["band_low"] == band["band_index"] * 0.1
            assert band["band_high"] == (band["band_index"] + 1) * 0.1

    print("PASS")


if __name__ == "__main__":
    _self_test()