# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Forecast API.

GET /api/risk/forecast?days=30
Reads axis score history from mcp_llm_axis_scores, computes a daily time-series of
composite axis scores per server, fits a simple linear regression trend, extrapolates
forward by `days`, maps the projected probabilities to a risk tier, and returns the
forecast with confidence bands.

Auth: public.
Data: app tier via get_session + SQLAlchemy ORM (McpLlmAxisScore, McpServerRegistry).
"""
from __future__ import annotations

import sys as _sys
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk_tier_forecast_api"])


# --------------------------------------------------------------------------- #
# Pydantic shapes
# --------------------------------------------------------------------------- #

class ForecastEntry(BaseModel):
    date: str = Field(..., description="ISO date of the forecast point (YYYY-MM-DD)")
    tier: str = Field(..., description="Predicted risk tier at that point")
    predicted_count: int = Field(..., ge=0, description="Approximate server count in that tier")
    confidence_band: str = Field(..., description="high | mid | low extrapolation confidence")


class ForecastResponse(BaseModel):
    forecast: list[ForecastEntry] = Field(default_factory=list)
    days: int = Field(..., description="Look-ahead window requested")
    as_of: str = Field(..., description="ISO 8601 generation timestamp")


# --------------------------------------------------------------------------- #
# Core computation helpers
# --------------------------------------------------------------------------- #

def _compute_tier(p_critical: float, p_danger: float, p_top: float) -> str:
    """Map probability triplet to a risk tier label."""
    if p_critical > 0.6 or p_danger > 0.8:
        return "critical"
    if p_critical > 0.4 or p_danger > 0.6 or p_top > 0.9:
        return "high"
    if p_critical > 0.2 or p_danger > 0.4:
        return "medium"
    if p_critical > 0.1 or p_danger > 0.2 or p_top > 0.5:
        return "low"
    return "minimal"


def _confidence_band(error: float) -> str:
    """Map extrapolation error to a confidence band."""
    if error < 0.1:
        return "high"
    if error < 0.3:
        return "mid"
    return "low"


def _composite_score(p_critical: float, p_danger: float, p_top: float) -> float:
    """Weighted composite of the three probability fields."""
    return 0.5 * p_critical + 0.3 * p_danger + 0.2 * p_top


def _linear_trend(values: list[float], days_ahead: int) -> tuple[float, float]:
    """Fit OLS slope+intercept and extrapolate. Returns (predicted_value, std_error)."""
    n = len(values)
    if n < 2:
        return values[0] if values else 0.0, 1.0
    indices = list(range(n))
    sum_x = sum(indices)
    sum_y = sum(values)
    sum_xy = sum(x * y for x, y in zip(indices, values))
    sum_x2 = sum(x * x for x in indices)
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-10:
        slope, intercept = 0.0, sum_y / n
    else:
        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n
    predicted = intercept + slope * (n - 1 + days_ahead)
    residuals = [values[i] - (intercept + slope * i) for i in range(n)]
    variance = sum(r * r for r in residuals) / max(n - 2, 1)
    std_error = min(1.0, variance ** 0.5)
    return max(0.0, predicted), std_error


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get("/risk/forecast", response_model=ForecastResponse)
def get_forecast(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ForecastResponse:
    """Forecast risk tier distribution `days` ahead using linear-trend extrapolation."""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    # Build daily time-series per server from the most-recent model version
    latest_model = db.execute(
        select(McpLlmAxisScore.model_version)
        .distinct()
        .order_by(McpLlmAxisScore.model_version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not latest_model:
        return ForecastResponse(
            forecast=[ForecastEntry(
                date=(date.today() + timedelta(days=days)).isoformat(),
                tier="minimal",
                predicted_count=0,
                confidence_band="mid",
            )],
            days=days,
            as_of=datetime.utcnow().isoformat(),
        )

    rows = (
        db.execute(
            select(
                McpLlmAxisScore.server_id,
                func.date(McpLlmAxisScore.scored_at).label("day"),
                func.avg(McpLlmAxisScore.p_critical).label("avg_pc"),
                func.avg(McpLlmAxisScore.p_danger).label("avg_pd"),
                func.avg(McpLlmAxisScore.p_top).label("avg_pt"),
            )
            .where(McpLlmAxisScore.scored_at >= start_date)
            .where(McpLlmAxisScore.scored_at <= end_date)
            .where(McpLlmAxisScore.model_version == latest_model)
            .group_by(
                McpLlmAxisScore.server_id,
                func.date(McpLlmAxisScore.scored_at),
            )
            .order_by(McpLlmAxisScore.server_id, func.date(McpLlmAxisScore.scored_at))
        )
        .all()
    )

    if not rows:
        return ForecastResponse(
            forecast=[ForecastEntry(
                date=(date.today() + timedelta(days=days)).isoformat(),
                tier="minimal",
                predicted_count=0,
                confidence_band="mid",
            )],
            days=days,
            as_of=datetime.utcnow().isoformat(),
        )

    # Collect time-series per server
    series: dict[str, list[float]] = {}
    for row in rows:
        sid = str(row.server_id)
        score = _composite_score(
            float(row.avg_pc or 0),
            float(row.avg_pd or 0),
            float(row.avg_pt or 0),
        )
        series.setdefault(sid, []).append(score)

    forecast: list[ForecastEntry] = []
    base_date = date.today()

    for sid, values in series.items():
        if len(values) < 2:
            continue
        predicted, error = _linear_trend(values, days)
        p_c = min(1.0, max(0.0, predicted * 1.2))
        p_d = min(1.0, max(0.0, predicted * 0.8))
        p_t = min(1.0, max(0.0, predicted * 0.6))
        tier = _compute_tier(p_c, p_d, p_t)
        confidence = _confidence_band(error)
        forecast.append(ForecastEntry(
            date=(base_date + timedelta(days=days)).isoformat(),
            tier=tier,
            predicted_count=max(1, int(predicted * 100)),
            confidence_band=confidence,
        ))

    if not forecast:
        forecast.append(ForecastEntry(
            date=(base_date + timedelta(days=days)).isoformat(),
            tier="minimal",
            predicted_count=0,
            confidence_band="mid",
        ))

    forecast.sort(key=lambda e: (e.date, e.tier))

    return ForecastResponse(
        forecast=forecast,
        days=days,
        as_of=datetime.utcnow().isoformat(),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    def _override():
        return TestSessionLocal()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override
    client = TestClient(test_app)

    with TestSessionLocal() as sess:
        now = datetime.utcnow()
        for i, sid in enumerate(["srv-a", "srv-b", "srv-c"]):
            for d in range(10):
                offset = (i - 1) * 0.1 + d * 0.02
                sess.add(McpLlmAxisScore(
                    server_id=sid,
                    axis_name="overall_risk",
                    model_version="v1",
                    p_critical=0.05 + offset,
                    p_danger=0.10 + offset,
                    p_top=0.40 + offset,
                    scored_at=now - timedelta(days=9 - d),
                ))
        sess.commit()

    resp = client.get("/api/risk/forecast?days=7")
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}: {resp.text}")
        _sys.exit(1)
    data = resp.json()
    if "forecast" not in data:
        print("FAIL: missing 'forecast' key")
        _sys.exit(1)
    if not isinstance(data["forecast"], list):
        print("FAIL: 'forecast' must be a list")
        _sys.exit(1)
    for entry in data["forecast"]:
        for field in ("date", "tier", "predicted_count", "confidence_band"):
            if field not in entry:
                print(f"FAIL: entry missing '{field}': {entry}")
                _sys.exit(1)
        if entry["confidence_band"] not in ("high", "mid", "low"):
            print(f"FAIL: invalid confidence_band: {entry['confidence_band']}")
            _sys.exit(1)
        if entry["predicted_count"] < 0:
            print(f"FAIL: predicted_count must be >= 0: {entry['predicted_count']}")
            _sys.exit(1)

    if data.get("days") != 7:
        print(f"FAIL: days should be 7, got {data.get('days')}")
        _sys.exit(1)

    if not data.get("as_of"):
        print("FAIL: missing 'as_of' timestamp")
        _sys.exit(1)

    # Test with mesh unavailable -- app tier alone must still work
    resp2 = client.get("/api/risk/forecast?days=14")
    if resp2.status_code != 200:
        print(f"FAIL: forecast should succeed without mesh, got {resp2.status_code}")
        _sys.exit(1)

    # Auth is not required (public endpoint)
    resp3 = client.get("/api/risk/forecast?days=1")
    if resp3.status_code != 200:
        print(f"FAIL: public endpoint should not require auth: {resp3.status_code}")
        _sys.exit(1)

    print("PASS")
    _sys.exit(0)
