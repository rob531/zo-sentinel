# deps: fastapi, requests
"""Risk Trend API module.

Provides a FastAPI router exposing GET `/api/risk_trends/{server_id}`.
The endpoint queries the write_service HTTP API for historical risk data
from the `mcp_llm_axis_scores` and `mcp_risk_register` tables and returns a
JSON payload with daily aggregated risk information for the last 90 days.

All database access is performed via `requests.post` to
`http://127.0.0.1:8772/query` with parameterized SQL (no string interpolation).
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Dict, List, Any

import requests
from fastapi import APIRouter, FastAPI, HTTPException

# FastAPI router that can be included in a larger application.
router = APIRouter()

# Constants
_WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
_DAYS_BACK = 90


def _query_write_service(sql: str, params: List[Any]) -> List[Dict[str, Any]]:
    """Execute a parameterized query against the write_service.

    Args:
        sql: The SQL statement with placeholders (`?`).
        params: List of parameters corresponding to the placeholders.

    Returns:
        List of rows as dictionaries.
    """
    payload = {"sql": sql, "params": params}
    try:
        resp = requests.post(_WRITE_SERVICE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # The write_service returns a JSON object with a ``rows`` key.
        return data.get("rows", [])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Write service error: {exc}")


def _get_date_range() -> (str, str):
    """Return ISO‑8601 strings for today and the date N days ago."""
    today = datetime.date.today()
    start = today - datetime.timedelta(days=_DAYS_BACK)
    return today.isoformat(), start.isoformat()


def _aggregate_trends(
    axis_rows: List[Dict[str, Any]],
    risk_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Combine axis scores and risk register rows into daily trend dicts.

    The function groups rows by date, builds the axis_scores mapping, and
    attaches the overall risk information. Missing data for a day is omitted.
    """
    # Group axis scores by date
    axis_by_date: Dict[str, Dict[str, float]] = defaultdict(dict)
    for row in axis_rows:
        # Expected columns: date, axis, score
        d = row["date"]
        axis = row["axis"]
        score = float(row["score"])
        axis_by_date[d][axis] = score

    # Map risk register rows by date
    risk_by_date: Dict[str, Dict[str, Any]] = {}
    for row in risk_rows:
        d = row["date"]
        risk_by_date[d] = {
            "overall_risk": float(row["overall_score"]),
            "risk_tier": str(row["risk_tier"]),
        }

    # Build the combined list sorted by date descending (most recent first)
    trends: List[Dict[str, Any]] = []
    for date_str in sorted(risk_by_date.keys(), reverse=True):
        if date_str not in axis_by_date:
            # If there are no axis scores for this date we still include the risk info.
            axis_scores = {}
        else:
            axis_scores = axis_by_date[date_str]
        entry = {
            "date": date_str,
            "overall_risk": risk_by_date[date_str]["overall_risk"],
            "risk_tier": risk_by_date[date_str]["risk_tier"],
            "axis_scores": axis_scores,
        }
        trends.append(entry)
    return trends


@router.get("/api/risk_trends/{server_id}")
def get_risk_trends(server_id: str) -> Dict[str, Any]:
    """FastAPI endpoint returning risk trends for a given server.

    The response format matches the specification:
    ```json
    {
        "server_id": "<id>",
        "trends": [
            {
                "date": "YYYY-MM-DD",
                "overall_risk": <float>,
                "risk_tier": "<string>",
                "axis_scores": {"axis_name": <float>, ...}
            },
            ...
        ]
    }
    ```
    """
    today_iso, start_iso = _get_date_range()

    # Query axis scores
    axis_sql = (
        "SELECT date, axis, score FROM mcp_llm_axis_scores "
        "WHERE server_id = ? AND date >= ?"
    )
    axis_params = [server_id, start_iso]
    axis_rows = _query_write_service(axis_sql, axis_params)

    # Query overall risk register
    risk_sql = (
        "SELECT date, overall_score, risk_tier FROM mcp_risk_register "
        "WHERE server_id = ? AND date >= ?"
    )
    risk_params = [server_id, start_iso]
    risk_rows = _query_write_service(risk_sql, risk_params)

    trends = _aggregate_trends(axis_rows, risk_rows)
    if not trends:
        raise HTTPException(status_code=404, detail="No risk data found for server")
    return {"server_id": server_id, "trends": trends}


# ---------------------------------------------------------------------------
# Self‑test harness
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # The test harness patches ``requests.post`` to return deterministic data.
    from fastapi.testclient import TestClient

    class _MockResponse:
        def __init__(self, json_data: Dict[str, Any]):
            self._json = json_data

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Dict[str, Any]:
            return self._json

    def _mock_post(url: str, json: Dict[str, Any], timeout: int = 10) -> _MockResponse:
        sql = json.get("sql", "")
        params = json.get("params", [])
        # Simple mock based on which table is queried.
        if "FROM mcp_llm_axis_scores" in sql:
            # Return three days of axis scores for two axes.
            rows = []
            base_date = datetime.date.today()
            for i in range(3):
                d = (base_date - datetime.timedelta(days=i)).isoformat()
                rows.append({"date": d, "axis": "confidentiality", "score": 0.5 + i * 0.1})
                rows.append({"date": d, "axis": "integrity", "score": 0.6 + i * 0.1})
            return _MockResponse({"rows": rows})
        elif "FROM mcp_risk_register" in sql:
            rows = []
            base_date = datetime.date.today()
            for i in range(3):
                d = (base_date - datetime.timedelta(days=i)).isoformat()
                rows.append({"date": d, "overall_score": 0.7 + i * 0.05, "risk_tier": "medium"})
            return _MockResponse({"rows": rows})
        else:
            return _MockResponse({"rows": []})

    # Patch the requests.post used inside the module.
    requests.post = _mock_post  # type: ignore

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/risk_trends/test_server_id")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert data["server_id"] == "test_server_id"
    trends = data["trends"]
    assert isinstance(trends, list) and len(trends) >= 1
    for entry in trends:
        assert "date" in entry and isinstance(entry["date"], str)
        assert "overall_risk" in entry and isinstance(entry["overall_risk"], (int, float))
        assert "risk_tier" in entry and isinstance(entry["risk_tier"], str)
        assert "axis_scores" in entry and isinstance(entry["axis_scores"], dict)
        # At least one axis score should be present.
        assert len(entry["axis_scores"]) > 0
    print("PASS")
