# deps: requests
"""FastAPI router for GET /servers/scoring-delta-timeline?limit=50.
Reads recent axis score records per server via write_service /query, computes
label changes between consecutive scores, and returns top-N deltas.
"""

from __future__ import annotations

from collections import defaultdict
from typing import List, Literal

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["scoring_delta_timeline"])

# Define risk ordering for trajectory determination
_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

class DeltaRecord(BaseModel):
    server_id: str
    axis_name: str
    label: str
    scored_at: str  # ISO8601 timestamp string
    delta: int
    from_label: str
    to_label: str
    trajectory: Literal["IMPROVED", "DEGRADED", "STABLE"]

def _determine_trajectory(prev_label: str, cur_label: str) -> str:
    prev = _RISK_ORDER.get(prev_label.upper(), -1)
    cur = _RISK_ORDER.get(cur_label.upper(), -1)
    if prev == -1 or cur == -1:
        return "STABLE"
    if cur > prev:
        return "DEGRADED"
    if cur < prev:
        return "IMPROVED"
    return "STABLE"

@router.get("/servers/scoring-delta-timeline", response_model=List[DeltaRecord])
def scoring_delta_timeline(limit: int = Query(50, ge=1, le=200)) -> List[DeltaRecord]:
    """Return the top *limit* scoring deltas across all servers.
    The data source is the write_service HTTP endpoint.
    """
    sql = """
        SELECT server_id, axis_name, label, scored_at
        FROM mcp_llm_axis_scores
        ORDER BY server_id, axis_name, scored_at DESC
    """
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": []},
            timeout=10,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Data service error: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Data service error")
    rows = resp.json().get("rows", [])
    # Group rows by (server_id, axis_name) – assume rows are already sorted desc by scored_at
    groups: dict[tuple[str, str], List[dict]] = defaultdict(list)
    for row in rows:
        key = (row["server_id"], row["axis_name"])  # type: ignore[index]
        groups[key].append(row)
    deltas: List[DeltaRecord] = []
    for (sid, axis), recs in groups.items():
        # Keep only the most recent 20 records per group
        recent = recs[:20]
        for i in range(len(recent) - 1):
            cur = recent[i]
            prev = recent[i + 1]
            from_label = prev.get("label", "")
            to_label = cur.get("label", "")
            trajectory = _determine_trajectory(from_label, to_label)
            delta_val = 1 if trajectory != "STABLE" else 0
            deltas.append(
                DeltaRecord(
                    server_id=sid,
                    axis_name=axis,
                    label=to_label,
                    scored_at=cur.get("scored_at", ""),
                    delta=delta_val,
                    from_label=from_label,
                    to_label=to_label,
                    trajectory=trajectory,
                )
            )
    # Sort by delta descending, then most recent scored_at
    deltas.sort(key=lambda d: (d.delta, d.scored_at), reverse=True)
    return deltas[:limit]

if __name__ == "__main__":  # CI-safe self-test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import Mock

    # Mock the requests.post used in the endpoint
    def _mock_post(url, json, timeout=None):
        class _Resp:
            status_code = 200
            def json(self):
                # Sample rows: two servers, two axes, each with two recent scores
                return {
                    "rows": [
                        {"server_id": "srv1", "axis_name": "overall_risk", "label": "HIGH", "scored_at": "2024-01-02T12:00:00Z"},
                        {"server_id": "srv1", "axis_name": "overall_risk", "label": "MEDIUM", "scored_at": "2024-01-01T12:00:00Z"},
                        {"server_id": "srv1", "axis_name": "auth_strength", "label": "STRONG", "scored_at": "2024-01-02T12:00:00Z"},
                        {"server_id": "srv1", "axis_name": "auth_strength", "label": "WEAK", "scored_at": "2024-01-01T12:00:00Z"},
                        {"server_id": "srv2", "axis_name": "overall_risk", "label": "LOW", "scored_at": "2024-01-02T12:00:00Z"},
                        {"server_id": "srv2", "axis_name": "overall_risk", "label": "LOW", "scored_at": "2024-01-01T12:00:00Z"},
                    ]
                }
        return _Resp()

    # Patch requests.post globally for the test
    requests.post = _mock_post  # type: ignore

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.get("/api/servers/scoring-delta-timeline?limit=10")
    assert r.status_code == 200, f"Bad status: {r.status_code} {r.text}"
    data = r.json()
    assert isinstance(data, list), "Response not a list"
    for rec in data:
        assert rec["trajectory"] in {"IMPROVED", "DEGRADED", "STABLE"}, f"Bad trajectory {rec}"
    print("PASS")
