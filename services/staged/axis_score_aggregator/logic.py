import threading
import time
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
import requests

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis-scores", tags=["axis-scores"])


class AxisScoreSummaryResponse(BaseModel):
    axis_name: str
    count: int
    min_p_top: float
    max_p_top: float
    avg_p_top: float
    p50: float
    p95: float
    critical_count: int
    escalated_count: int

    class Config:
        from_attributes = True


def calculate_percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    k = (n - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < n else f
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def aggregate(session, axis_scores_store: Optional[Dict[int, Dict]] = None, servers_store: Optional[Dict[int, Dict]] = None) -> List[AxisScoreSummaryResponse]:
    if axis_scores_store is not None and servers_store is not None:
        rows = []
        for score_id, score in axis_scores_store.items():
            server = servers_store.get(score["server_id"])
            if server:
                rows.append({"axis_name": score["axis_name"], "p_top": score["p_top"], "escalated": score["escalated"]})
    else:
        results = (
            session.query(McpLlmAxisScore.axis_name, McpLlmAxisScore.p_top, McpLlmAxisScore.escalated)
            .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
            .all()
        )
        rows = [{"axis_name": r.axis_name, "p_top": r.p_top, "escalated": r.escalated} for r in results]

    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for row in rows:
        grouped[row["axis_name"]].append(row)

    summaries = []
    for axis_name, axis_rows in grouped.items():
        p_top_values = [r["p_top"] for r in axis_rows]
        sorted_p_top = sorted(p_top_values)
        count = len(p_top_values)
        min_p_top = min(p_top_values) if p_top_values else 0.0
        max_p_top = max(p_top_values) if p_top_values else 0.0
        avg_p_top = sum(p_top_values) / count if count > 0 else 0.0
        p50 = calculate_percentile(sorted_p_top, 0.5)
        p95 = calculate_percentile(sorted_p_top, 0.95)
        critical_count = sum(1 for v in p_top_values if v >= 0.7)
        escalated_count = sum(1 for r in axis_rows if r["escalated"])

        summary = AxisScoreSummaryResponse(
            axis_name=axis_name,
            count=count,
            min_p_top=min_p_top,
            max_p_top=max_p_top,
            avg_p_top=avg_p_top,
            p50=p50,
            p95=p95,
            critical_count=critical_count,
            escalated_count=escalated_count,
        )
        summaries.append(summary)

        payload = {
            "axis_name": axis_name,
            "count": count,
            "min_p_top": min_p_top,
            "max_p_top": max_p_top,
            "avg_p_top": avg_p_top,
            "p50": p50,
            "p95": p95,
            "critical_count": critical_count,
            "escalated_count": escalated_count,
            "scored_at": datetime.now().isoformat(),
        }
        try:
            requests.post(
                "http://127.0.0.1:8772/write/mcp_axis_score_summaries",
                json=payload,
                timeout=5,
            )
        except requests.RequestException:
            pass

    return summaries


@router.get("/summary", response_model=List[AxisScoreSummaryResponse])
def get_summary(session=Depends(get_session)) -> List[AxisScoreSummaryResponse]:
    return aggregate(session)


def heartbeat():
    while True:
        try:
            requests.get("http://127.0.0.1:8772/health", timeout=5)
        except requests.RequestException:
            pass
        time.sleep(60)


def cycle(cadence_seconds: int = 3600):
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    while True:
        aggregate(None)
        time.sleep(cadence_seconds)


if __name__ == "__main__":
    from fastapi import FastAPI

    class MockObj:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    test_servers = {
        1: {"server_id": 1, "name": "server-a"},
        2: {"server_id": 2, "name": "server-b"},
        3: {"server_id": 3, "name": "server-c"},
    }

    test_scores = [
        {"id": 1, "server_id": 1, "axis_name": "security", "p_top": 0.2, "escalated": False, "probs": "[0.2,0.3,0.5]"},
        {"id": 2, "server_id": 1, "axis_name": "security", "p_top": 0.4, "escalated": False, "probs": "[0.2,0.4,0.4]"},
        {"id": 3, "server_id": 2, "axis_name": "security", "p_top": 0.6, "escalated": False, "probs": "[0.1,0.3,0.6]"},
        {"id": 4, "server_id": 2, "axis_name": "security", "p_top": 0.7, "escalated": False, "probs": "[0.1,0.2,0.7]"},
        {"id": 5, "server_id": 3, "axis_name": "security", "p_top": 0.8, "escalated": True, "probs": "[0.1,0.1,0.8]"},
        {"id": 6, "server_id": 1, "axis_name": "reliability", "p_top": 0.1, "escalated": False, "probs": "[0.1,0.4,0.5]"},
        {"id": 7, "server_id": 2, "axis_name": "reliability", "p_top": 0.2, "escalated": False, "probs": "[0.2,0.3,0.5]"},
        {"id": 8, "server_id": 2, "axis_name": "reliability", "p_top": 0.3, "escalated": False, "probs": "[0.3,0.3,0.4]"},
        {"id": 9, "server_id": 3, "axis_name": "reliability", "p_top": 0.6, "escalated": False, "probs": "[0.1,0.3,0.6]"},
        {"id": 10, "server_id": 3, "axis_name": "reliability", "p_top": 0.9, "escalated": False, "probs": "[0.0,0.1,0.9]"},
    ]

    axis_scores_store = {s["id"]: s for s in test_scores}

    summaries = aggregate(None, axis_scores_store, test_servers)

    axis_names = set(s.axis_name for s in summaries)
    assert len(axis_names) == 2, f"Expected 2 axes, got {len(axis_names)}"

    for s in summaries:
        assert 0 <= s.p50 <= 1, f"p50 out of range: {s.p50}"
        assert 0 <= s.p95 <= 1, f"p95 out of range: {s.p95}"
        assert 0 <= s.min_p_top <= 1, f"min_p_top out of range: {s.min_p_top}"
        assert 0 <= s.max_p_top <= 1, f"max_p_top out of range: {s.max_p_top}"
        assert 0 <= s.avg_p_top <= 1, f"avg_p_top out of range: {s.avg_p_top}"

    print("PASS")