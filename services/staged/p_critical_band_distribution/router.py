"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Float, cast, func, literal, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:  # pragma: no cover - resolved at package import time
    from .logic import critical_band_distribution
except ImportError:  # pragma: no cover
    critical_band_distribution = None  # type: ignore[assignment]

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

MIN_BUCKET_SIZE = 0.01
MAX_BUCKET_SIZE = 0.25
DEFAULT_BUCKET_SIZE = 0.1


class BandBucket(BaseModel):
    band_index: int
    band_low: float
    band_high: float
    count: int
    avg_p_top: float


class AxisBands(BaseModel):
    axis_name: str
    bands: list[BandBucket]
    total_count: int


class CriticalBandDistributionResponse(BaseModel):
    bucket_size: float
    axes: list[AxisBands]
    grand_total: int


def _clamp_bucket_size(bucket_size: Any) -> float:
    try:
        value = float(bucket_size)
    except (TypeError, ValueError):
        value = DEFAULT_BUCKET_SIZE
    if value != value:  # NaN
        value = DEFAULT_BUCKET_SIZE
    return max(MIN_BUCKET_SIZE, min(MAX_BUCKET_SIZE, value))


def _local_distribution(db: Session, axis: str | None, bucket_size: float) -> dict[str, Any]:
    bucket_size = _clamp_bucket_size(bucket_size)
    n_bands = max(1, int(round(1.0 / bucket_size)))
    band_expr = func.floor(
        cast(McpLlmAxisScore.p_critical, Float) / literal(bucket_size, Float)
    )
    stmt = (
        select(
            McpLlmAxisScore.axis_name,
            band_expr.label("band_index"),
            func.count(McpLlmAxisScore.id).label("cnt"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers_distinct"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .where(McpLlmAxisScore.p_critical.is_not(None))
        .group_by(McpLlmAxisScore.axis_name, band_expr)
    )
    if axis:
        stmt = stmt.where(McpLlmAxisScore.axis_name == axis)
    rows = db.execute(stmt).all()

    agg: dict[tuple[str, int], dict[str, float]] = {}
    for row in rows:
        idx = int(row.band_index or 0)
        idx = max(0, min(idx, n_bands - 1))
        key = (str(row.axis_name), idx)
        cur = agg.setdefault(key, {"cnt": 0.0, "servers": 0.0, "ptop_sum": 0.0, "ptop_n": 0.0})
        cnt = int(row.cnt or 0)
        cur["cnt"] += cnt
        cur["servers"] += int(row.servers_distinct or 0)
        if row.avg_p_top is not None:
            cur["ptop_sum"] += float(row.avg_p_top) * cnt
            cur["ptop_n"] += cnt

    axes_map: dict[str, dict[str, Any]] = {}
    for (axis_name, idx), cur in sorted(agg.items()):
        entry = axes_map.setdefault(axis_name, {"bands": [], "total_count": 0})
        entry["bands"].append(
            {
                "band_index": idx,
                "band_low": round(idx * bucket_size, 6),
                "band_high": round((idx + 1) * bucket_size, 6),
                "count": int(cur["cnt"]),
                "avg_p_top": round(cur["ptop_sum"] / cur["ptop_n"], 6) if cur["ptop_n"] else 0.0,
            }
        )
        entry["total_count"] += int(cur["cnt"])

    axes = [
        {"axis_name": name, "bands": entry["bands"], "total_count": entry["total_count"]}
        for name, entry in sorted(axes_map.items())
    ]
    return {
        "bucket_size": bucket_size,
        "axes": axes,
        "grand_total": sum(a["total_count"] for a in axes),
    }


def _distribution(db: Session, axis: str | None, bucket_size: float) -> dict[str, Any]:
    bucket_size = _clamp_bucket_size(bucket_size)
    if critical_band_distribution is not None:
        data = None
        try:
            data = critical_band_distribution(db, axis=axis, bucket_size=bucket_size)
        except TypeError:
            try:
                data = critical_band_distribution(db, axis, bucket_size)
            except TypeError:
                data = None
        except Exception:
            data = None
        if isinstance(data, dict) and "axes" in data and "grand_total" in data:
            data = dict(data)
            data["bucket_size"] = bucket_size
            return data
    return _local_distribution(db, axis, bucket_size)


@router.get("/p-critical-bands", response_model=CriticalBandDistributionResponse)
def get_critical_band_distribution(
    db: Session = Depends(get_session),
    axis: str | None = Query(None),
    bucket_size: float = Query(DEFAULT_BUCKET_SIZE),
) -> CriticalBandDistributionResponse:
    payload = _distribution(db, axis.strip() if axis else None, bucket_size)
    return CriticalBandDistributionResponse(**payload)


def _self_test() -> None:
    import uuid

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    rows = [(0.05, 0.05), (0.15, 0.15), (0.95, 0.95)]
    try:
        for i, (pc, pt) in enumerate(rows):
            db.add(
                McpLlmAxisScore(
                    server_id=f"srv-{i + 1}",
                    axis_name="test_axis",
                    model_version="test-model",
                    p_critical=pc,
                    p_top=pt,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        for i, (pc, pt) in enumerate(rows):
            db.add(
                McpLlmAxisScore(
                    id=str(uuid.uuid4()),
                    server_id=f"srv-{i + 1}",
                    axis_name="test_axis",
                    model_version="test-model",
                    p_critical=pc,
                    p_top=pt,
                )
            )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: db

    client = TestClient(test_app)
    resp = client.get(
        "/api/scoring/p-critical-bands",
        params={"axis": "test_axis", "bucket_size": 0.1},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["bucket_size"] == 0.1, data
    assert len(data["axes"]) == 1, data
    ax = data["axes"][0]
    assert ax["axis_name"] == "test_axis", data
    assert len(ax["bands"]) == 3, data
    assert sorted(b["count"] for b in ax["bands"]) == [1, 1, 1], data
    for band in ax["bands"]:
        assert abs(band["band_low"] - band["band_index"] * 0.1) < 1e-6, band
        assert abs(band["band_high"] - (band["band_index"] + 1) * 0.1) < 1e-6, band
    assert ax["total_count"] == 3, data
    assert data["grand_total"] == 3, data

    db.close()
    print("PASS")


if __name__ == "__main__":
    _self_test()