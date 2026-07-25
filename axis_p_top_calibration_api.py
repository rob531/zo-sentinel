import json
import bisect
import datetime
import enum
import httpx
import asyncio
from typing import List, Dict, Optional, Any

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, conint, validator

# Application imports (must use the real app DB session and models)
from app.db import get_session
from app.models import McpLlmAxisScore  # type: ignore

router = APIRouter()


# ----------------------------------------------------------------------
# Constants & Enums
# ----------------------------------------------------------------------
AXES = [
    "clarity",
    "relevance",
    "accuracy",
    "completeness",
    "conciseness",
    "originality",
    "style",
]


class AxisEnum(str, enum.Enum):
    clarity = "clarity"
    relevance = "relevance"
    accuracy = "accuracy"
    completeness = "completeness"
    conciseness = "conciseness"
    originality = "originality"
    style = "style"


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class BinInfo(BaseModel):
    lower: float
    upper: float


class AxisStats(BaseModel):
    counts: List[int] = Field(..., description="Histogram bucket counts")
    total_rows: int = Field(..., description="Number of rows for this axis")
    mean_p_top: Optional[float] = Field(None, description="Mean of p_top")
    mean_p_critical: Optional[float] = Field(None, description="Mean of p_critical")
    mean_p_danger: Optional[float] = Field(None, description="Mean of p_danger")
    max_p_top: Optional[float] = Field(None, description="Maximum p_top observed")


class CalibrationResponse(BaseModel):
    bins: List[BinInfo]
    per_axis: Dict[str, AxisStats]
    generated_at: datetime.datetime


# ----------------------------------------------------------------------
# Helper: fetch rows via the external /query service
# ----------------------------------------------------------------------
_QUERY_URL = "http://127.0.0.1:8772/query"
_MAX_RETRIES = 3
_BASE_TIMEOUT = 10.0  # seconds for external call
_QUERY_TIMEOUT = 30.0  # seconds for /query endpoint


async def _fetch_rows(
    axis_name: Optional[str] = None,
    decision_rule_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Execute a read‑only SELECT against the external query service.
    Retries with exponential back‑off up to three times.
    """
    sql = (
        "SELECT axis_name, p_top, p_critical, p_danger, decision_rule_version "
        "FROM mcp_llm_axis_scores"
    )
    params: List[Any] = []
    conditions: List[str] = []

    if axis_name:
        conditions.append("axis_name = ?")
        params.append(axis_name)
    if decision_rule_version:
        conditions.append("decision_rule_version = ?")
        params.append(decision_rule_version)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    payload = {"sql": sql, "params": params}
    backoff = 1.0
    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=_BASE_TIMEOUT) as client:
                resp = await client.post(_QUERY_URL, json=payload, timeout=_QUERY_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                # Expecting {"rows": [...]}
                return data.get("rows", [])
        except (httpx.HTTPError, json.JSONDecodeError):
            if attempt == _MAX_RETRIES - 1:
                raise
            await asyncio.sleep(backoff)
            backoff *= 3.0
    return []  # Unreachable


# ----------------------------------------------------------------------
# Core endpoint
# ----------------------------------------------------------------------
@router.get(
    "/axes/calibration",
    response_model=CalibrationResponse,
    summary="Calibration diagnostics for LLM rubric axes",
)
async def get_axes_calibration(
    axis_name: Optional[AxisEnum] = Query(
        None, description="Filter to a single axis"
    ),
    bins: conint(ge=1, le=50) = Query(
        10, description="Number of histogram bins (1‑50)"
    ),
    decision_rule_version: Optional[str] = Query(
        None, description="Optional decision rule version filter"
    ),
    session=Depends(get_session),
):
    # ------------------------------------------------------------------
    # 1. Fetch data
    # ------------------------------------------------------------------
    rows = await _fetch_rows(
        axis_name=axis_name.value if axis_name else None,
        decision_rule_version=decision_rule_version,
    )

    # ------------------------------------------------------------------
    # 2. Prepare bin edges
    # ------------------------------------------------------------------
    bin_edges = [i / bins for i in range(bins + 1)]
    bin_infos = [
        BinInfo(lower=bin_edges[i], upper=bin_edges[i + 1]) for i in range(bins)
    ]

    # ------------------------------------------------------------------
    # 3. Initialise per‑axis structures
    # ------------------------------------------------------------------
    per_axis: Dict[str, AxisStats] = {
        axis: AxisStats(
            counts=[0] * bins,
            total_rows=0,
            mean_p_top=None,
            mean_p_critical=None,
            mean_p_danger=None,
            max_p_top=None,
        )
        for axis in AXES
    }

    # ------------------------------------------------------------------
    # 4. Accumulate statistics
    # ------------------------------------------------------------------
    sums: Dict[str, Dict[str, float]] = {
        axis: {"p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0, "max_p_top": 0.0}
        for axis in AXES
    }

    for row in rows:
        axis = row["axis_name"]
        if axis not in AXES:
            continue  # ignore unexpected axes
        stats = per_axis[axis]
        stats.total_rows += 1

        # Histogram binning for p_top
        p_top = row["p_top"]
        idx = bisect.bisect_left(bin_edges, p_top) - 1
        if idx < 0:
            idx = 0
        elif idx >= bins:
            idx = bins - 1
        stats.counts[idx] += 1

        # Running sums for means and max
        sums[axis]["p_top"] += p_top
        sums[axis]["p_critical"] += row["p_critical"]
        sums[axis]["p_danger"] += row["p_danger"]
        if p_top > sums[axis]["max_p_top"]:
            sums[axis]["max_p_top"] = p_top

    # ------------------------------------------------------------------
    # 5. Finalise means and maxes
    # ------------------------------------------------------------------
    for axis, stats in per_axis.items():
        total = stats.total_rows
        if total > 0:
            stats.mean_p_top = round(sums[axis]["p_top"] / total, 6)
            stats.mean_p_critical = round(sums[axis]["p_critical"] / total, 6)
            stats.mean_p_danger = round(sums[axis]["p_danger"] / total, 6)
            stats.max_p_top = round(sums[axis]["max_p_top"], 6)

    # ------------------------------------------------------------------
    # 6. Build response
    # ------------------------------------------------------------------
    response = CalibrationResponse(
        bins=bin_infos,
        per_axis=per_axis,
        generated_at=datetime.datetime.utcnow(),
    )
    return response


# ----------------------------------------------------------------------
# Self‑test when run as script
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import random
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Deterministic seed rows (30 rows across 3 axes)
    deterministic_rows = []
    axes_cycle = ["clarity", "relevance", "accuracy"]
    for i in range(30):
        axis = axes_cycle[i % len(axes_cycle)]
        p_top = round(0.05 + i * (0.90 / 29), 6)  # 0.05 .. 0.95 inclusive
        row = {
            "axis_name": axis,
            "p_top": p_top,
            "p_critical": max(0.0, p_top - 0.1),
            "p_danger": max(0.0, p_top - 0.2),
            "decision_rule_version": "v1",
        }
        deterministic_rows.append(row)

    # Monkey‑patch the fetch function to return our deterministic data
    async def _mock_fetch_rows(
        axis_name: Optional[str] = None,
        decision_rule_version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if axis_name:
            return [r for r in deterministic_rows if r["axis_name"] == axis_name]
        if decision_rule_version:
            return [r for r in deterministic_rows if r["decision_rule_version"] == decision_rule_version]
        return deterministic_rows

    # Replace the real fetch with the mock
    import types

    globals()["_fetch_rows"] = _mock_fetch_rows  # type: ignore

    # Build FastAPI app and include router
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # 1. General request (no axis filter)
    resp = client.get("/axes/calibration?bins=10")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    per_axis = data["per_axis"]
    total_counts = sum(sum(ax["counts"]) for ax in per_axis.values())
    assert total_counts == 30, f"Total counts {total_counts} != 30"
    # Mean p_top across all rows should be ~0.5
    all_p_tops = [r["p_top"] for r in deterministic_rows]
    expected_mean = sum(all_p_tops) / len(all_p_tops)
    mean_from_resp = sum(
        ax["mean_p_top"] * ax["total_rows"] for ax in per_axis.values() if ax["total_rows"]
    ) / 30
    assert abs(mean_from_resp - expected_mean) < 0.02, "Mean p_top out of tolerance"

    # 2. Axis filter request
    resp_axis = client.get("/axes/calibration?axis_name=clarity&bins=10")
    assert resp_axis.status_code == 200, f"Axis filter status {resp_axis.status_code}"
    data_axis = resp_axis.json()
    per_axis_filt = data_axis["per_axis"]
    # Only one axis should have non‑zero total_rows
    non_zero_axes = [k for k, v in per_axis_filt.items() if v["total_rows"] > 0]
    assert non_zero_axes == ["clarity"], f"Filtered axes {non_zero_axes}"
    # All 7 axis keys must be present
    assert set(per_axis_filt.keys()) == set(AXES), "Missing axis keys in filtered response"

    print("PASS")