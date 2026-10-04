# deps: fastapi, sqlalchemy, pydantic
"""Axis Score Distribution Report.

Returns distribution statistics for LLM axis scores across all servers:
mean, median, percentiles, min/max, stddev, and outlier counts per axis.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_score_distribution_report"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisDistributionStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: str
    count: int
    mean: float
    median: float
    p25: float
    p75: float
    min: float
    max: float
    stddev: float
    outlier_count: int


class AxisScoreDistributionReportResponse(BaseModel):
    generated_at: str
    window_days: int
    axes: List[AxisDistributionStats]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _compute_stats(values: List[float]) -> dict:
    """Compute mean, median, p25, p75, min, max, stddev, outlier_count from a list of floats."""
    if not values:
        return {
            "count": 0, "mean": 0.0, "median": 0.0,
            "p25": 0.0, "p75": 0.0, "min": 0.0, "max": 0.0,
            "stddev": 0.0, "outlier_count": 0,
        }
    n = len(values)
    mean = sum(values) / n
    sorted_vals = sorted(values)
    # median
    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    # p25 / p75
    p25_idx = max(0, int(n * 0.25))
    p75_idx = min(n - 1, int(n * 0.75))
    p25 = sorted_vals[p25_idx]
    p75 = sorted_vals[p75_idx]
    # min / max
    min_val = sorted_vals[0]
    max_val = sorted_vals[-1]
    # stddev
    variance = sum((v - mean) ** 2 for v in values) / n
    stddev = variance ** 0.5
    # outliers: values more than 2 stddev from mean
    threshold = 2 * stddev
    outlier_count = sum(1 for v in values if abs(v - mean) > threshold)

    return {
        "count": n,
        "mean": round(mean, 6),
        "median": round(median, 6),
        "p25": round(p25, 6),
        "p75": round(p75, 6),
        "min": round(min_val, 6),
        "max": round(max_val, 6),
        "stddev": round(stddev, 6),
        "outlier_count": outlier_count,
    }


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/axis-score-distribution-report",
    response_model=AxisScoreDistributionReportResponse,
    name="axis_score_distribution_report:report",
)
def get_axis_distribution_report(
    window_days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> AxisScoreDistributionReportResponse:
    """
    Return per-axis distribution statistics for p_top values in the
    configured lookback window (default 30 days).

    Each axis record includes: count, mean, median, p25, p75, min, max,
    stddev, and outlier_count (>2 stddev from mean).
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=window_days)

    # Fetch all rows within the window
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .all()
    )

    # Group by axis_name
    groups: dict[str, dict[str, float | str]] = {}
    for row in rows:
        axis = row.axis_name
        if axis not in groups:
            groups[axis] = {"label": row.label or "", "p_top_values": []}
        if row.p_top is not None:
            groups[axis]["p_top_values"].append(float(row.p_top))

    # Build response
    axes: List[AxisDistributionStats] = []
    for axis_name in sorted(groups.keys()):
        info = groups[axis_name]
        stats = _compute_stats(info["p_top_values"])
        axes.append(
            AxisDistributionStats(
                axis_name=axis_name,
                label=info["label"],
                **stats,
            )
        )

    return AxisScoreDistributionReportResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_days=window_days,
        axes=axes,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base, McpLlmAxisScore
    from app.db import get_session as _real_get_session

    # Build isolated FastAPI app for self-test
    _app = FastAPI()
    _app.include_router(router)

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(_engine)
    _TestSession = sessionmaker(bind=_engine)
    _test_db = _TestSession()

    # Seed test data: two axes with known p_top distributions
    _test_data = [
        # axis: overall_risk — 5 values [0.1, 0.2, 0.3, 0.4, 0.5]
        McpLlmAxisScore(
            server_id="srv-a",
            axis_name="overall_risk",
            label="LOW",
            label_index=0,
            p_top=0.1,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
        McpLlmAxisScore(
            server_id="srv-b",
            axis_name="overall_risk",
            label="LOW",
            label_index=0,
            p_top=0.2,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
        McpLlmAxisScore(
            server_id="srv-c",
            axis_name="overall_risk",
            label="MEDIUM",
            label_index=1,
            p_top=0.3,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
        McpLlmAxisScore(
            server_id="srv-d",
            axis_name="overall_risk",
            label="MEDIUM",
            label_index=1,
            p_top=0.4,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
        McpLlmAxisScore(
            server_id="srv-e",
            axis_name="overall_risk",
            label="HIGH",
            label_index=2,
            p_top=0.5,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
        # axis: auth_strength — 3 values [0.8, 0.9, 1.0]
        McpLlmAxisScore(
            server_id="srv-a",
            axis_name="auth_strength",
            label="STRONG",
            label_index=0,
            p_top=0.8,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
        McpLlmAxisScore(
            server_id="srv-b",
            axis_name="auth_strength",
            label="STRONG",
            label_index=0,
            p_top=0.9,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
        McpLlmAxisScore(
            server_id="srv-c",
            axis_name="auth_strength",
            label="WEAK",
            label_index=2,
            p_top=1.0,
            model_version="model-v1",
            decision_rule_version="v1",
            adapter_sha256="sha",
            scored_at=datetime.utcnow(),
        ),
    ]

    for row in _test_data:
        _test_db.add(row)
    _test_db.commit()

    def _override():
        try:
            yield _test_db
        finally:
            pass

    _app.dependency_overrides[_real_get_session] = _override
    _client = TestClient(_app)

    # --- Happy path: report returns two axes with correct structure ---
    _resp = _client.get("/api/axis-score-distribution-report")
    if _resp.status_code != 200:
        print(f"FAIL: status {_resp.status_code}: {_resp.text}")
        _sys.exit(1)
    _data = _resp.json()
    if "axes" not in _data or "generated_at" not in _data or "window_days" not in _data:
        print(f"FAIL: missing top-level keys in response: {_data}")
        _sys.exit(1)
    if len(_data["axes"]) != 2:
        print(f"FAIL: expected 2 axes, got {len(_data['axes'])}: {_data['axes']}")
        _sys.exit(1)
    for _ax in _data["axes"]:
        for _field in ("axis_name", "label", "count", "mean", "median",
                       "p25", "p75", "min", "max", "stddev", "outlier_count"):
            if _field not in _ax:
                print(f"FAIL: missing field '{_field}' in axis: {_ax}")
                _sys.exit(1)

    # --- Validation: window_days must be positive ---
    _resp2 = _client.get("/api/axis-score-distribution-report?window_days=0")
    if _resp2.status_code != 422:
        print(f"FAIL: expected 422 for window_days=0, got {_resp2.status_code}")
        _sys.exit(1)

    # --- Auth/permission failure: clear override and expect non-200 ---
    _app.dependency_overrides.clear()
    _resp3 = _client.get("/api/axis-score-distribution-report")
    if _resp3.status_code == 200:
        print(f"FAIL: expected non-200 without session override, got {_resp3.status_code}")
        _sys.exit(1)

    print("PASS")
