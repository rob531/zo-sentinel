import random
from typing import List

import requests
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class AxisScoreSummary(BaseModel):
    axis_name: str
    count: int
    min: float
    max: float
    avg: float
    p50: float
    p95: float
    critical_count: int
    escalated_count: int
    avg_p_top: float


def _compute_summary(session: Session) -> List[AxisScoreSummary]:
    scores = list(session.query(McpLlmAxisScore))
    data = {}
    for s in scores:
        axis = s.axis_name
        entry = data.setdefault(
            axis,
            {
                "count": 0,
                "p_vals": [],
                "p_top_vals": [],
                "critical": 0,
                "escalated": 0,
            },
        )
        entry["count"] += 1
        entry["p_vals"].append(s.p_top)
        entry["p_top_vals"].append(s.p_top)
        if getattr(s, "p_critical", None) is not None:
            entry["critical"] += 1
        if getattr(s, "escalated", False):
            entry["escalated"] += 1

    summaries: List[AxisScoreSummary] = []
    for axis, d in data.items():
        vals = d["p_vals"]
        vals_sorted = sorted(vals)
        cnt = d["count"]
        min_v = vals_sorted[0]
        max_v = vals_sorted[-1]
        avg = sum(vals) / cnt
        p50 = vals_sorted[int(0.5 * (cnt - 1))]
        p95 = vals_sorted[int(0.95 * (cnt - 1))]
        avg_p_top = sum(d["p_top_vals"]) / cnt
        summaries.append(
            AxisScoreSummary(
                axis_name=axis,
                count=cnt,
                min=min_v,
                max=max_v,
                avg=avg,
                p50=p50,
                p95=p95,
                critical_count=d["critical"],
                escalated_count=d["escalated"],
                avg_p_top=avg_p_top,
            )
        )
    return summaries


def _write_summary(summaries: List[AxisScoreSummary]) -> None:
    url = "http://127.0.0.1:8772/write?table=mcp_axis_score_summaries"
    payload = [s.dict() for s in summaries]
    resp = requests.post(url, json=payload)
    resp.raise_for_status()


def run_aggregation(session: Session) -> None:
    summaries = _compute_summary(session)
    _write_summary(summaries)


@router.get("/api/axis-scores/summary", response_model=List[AxisScoreSummary])
def get_latest_summary(session: Session = Depends(get_session)):
    return _compute_summary(session)


# ----------------------------------------------------------------------
# In‑memory test helpers
# ----------------------------------------------------------------------
class _InMemorySession:
    def __init__(self, axis_scores: List[McpLlmAxisScore], servers: List[McpServerRegistry]):
        self._axis_scores = axis_scores
        self._servers = servers

    def query(self, model):
        if model is McpLlmAxisScore:
            return self._axis_scores
        if model is McpServerRegistry:
            return self._servers
        return []


if __name__ == "__main__":
    # seed test data
    axis_scores = []
    servers = []
    for sid in range(1, 4):
        servers.append(
            type(
                "Srv",
                (),
                {
                    "server_id": sid,
                    "name": f"server{sid}",
                },
            )
        )
    for i in range(10):
        axis = "security" if i % 2 == 0 else "performance"
        axis_scores.append(
            type(
                "Score",
                (),
                {
                    "axis_name": axis,
                    "p_top": random.random(),
                    "p_critical": random.random(),
                    "escalated": random.choice([True, False]),
                    "server_id": random.choice([1, 2, 3]),
                },
            )
        )

    # capture write payload
    _captured = {}

    def _fake_post(url, json):
        _captured["data"] = json
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

        return _Resp()

    requests.post = _fake_post

    # run aggregation against in‑memory store
    run_aggregation(_InMemorySession(axis_scores, servers))

    summaries = _captured.get("data", [])
    assert len(summaries) == 2, f"expected 2 axes, got {len(summaries)}"
    for s in summaries:
        assert 0.0 <= s["avg_p_top"] <= 1.0, "avg_p_top out of bounds"
    print("PASS")