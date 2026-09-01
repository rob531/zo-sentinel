from typing import Dict, Generator, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class AxisInfo(BaseModel):
    total: int
    avg_score: float


class Summary(BaseModel):
    total: int
    by_tier: Dict[str, int]
    by_axis: Dict[str, AxisInfo]


class SummaryResponse(BaseModel):
    summary: Summary


def _tier_from_score(avg: float) -> str:
    """Simple tier classification."""
    if avg < 0.33:
        return "low"
    if avg < 0.66:
        return "medium"
    return "high"


@router.get("/scores/summary", response_model=SummaryResponse)
def get_summary(session: Generator = Depends(get_session)):
    """
    Return a summary of risk scores across all servers.

    The summary contains:
    * total – number of distinct servers
    * by_tier – count of servers per risk tier (low/medium/high)
    * by_axis – for each axis, total number of scores and average p_top score
    """
    rows: List[McpLlmAxisScore] = session.query(McpLlmAxisScore).all()

    # aggregate per axis
    axis_counts: Dict[str, int] = {}
    axis_sums: Dict[str, float] = {}

    # aggregate per server for tier calculation
    server_scores: Dict[int, List[float]] = {}

    for row in rows:
        axis = row.axis_name
        score = float(row.p_top) if row.p_top is not None else 0.0

        # axis aggregation
        axis_counts[axis] = axis_counts.get(axis, 0) + 1
        axis_sums[axis] = axis_sums.get(axis, 0.0) + score

        # server aggregation
        srv = row.server_id
        server_scores.setdefault(srv, []).append(score)

    # build by_axis dict
    by_axis: Dict[str, AxisInfo] = {}
    for axis, cnt in axis_counts.items():
        avg = axis_sums[axis] / cnt if cnt else 0.0
        by_axis[axis] = AxisInfo(total=cnt, avg_score=avg)

    # compute tiers
    tier_counts: Dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    for scores in server_scores.values():
        avg_srv = sum(scores) / len(scores) if scores else 0.0
        tier = _tier_from_score(avg_srv)
        tier_counts[tier] += 1

    total_servers = len(server_scores)

    summary = Summary(
        total=total_servers,
        by_tier={k: v for k, v in tier_counts.items() if v > 0},
        by_axis=by_axis,
    )
    return SummaryResponse(summary=summary)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a temporary in‑memory DB using the real models
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base  # the declarative base used by the real models

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Seed data
    def seed():
        sess = TestSession()
        data = [
            # server 1
            McpLlmAxisScore(
                server_id=1,
                axis_name="security",
                p_top=0.9,
                adapter_sha256="a"*64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=1,
                label="sec",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                probs="{}",
                scored_at=None,
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="performance",
                p_top=0.4,
                adapter_sha256="b"*64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=2,
                label="perf",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                probs="{}",
                scored_at=None,
            ),
            # server 2
            McpLlmAxisScore(
                server_id=2,
                axis_name="security",
                p_top=0.2,
                adapter_sha256="c"*64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=3,
                label="sec",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                probs="{}",
                scored_at=None,
            ),
            McpLlmAxisScore(
                server_id=2,
                axis_name="performance",
                p_top=0.3,
                adapter_sha256="d"*64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=4,
                label="perf",
                label_index=0,
                model_version="m1",
                p_critical=0.1,
                p_danger=0.2,
                probs="{}",
                scored_at=None,
            ),
        ]
        sess.add_all(data)
        sess.commit()
        sess.close()

    seed()

    # Dependency override
    def get_test_session():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)
    resp = client.get("/api/scores/summary")
    if resp.status_code != 200:
        print("FAIL: unexpected status", resp.status_code, file=sys.stderr)
        sys.exit(1)

    payload = resp.json()
    try:
        summary = payload["summary"]
        assert summary["total"] == 2
        assert sum(summary["by_tier"].values()) == 2
        assert "security" in summary["by_axis"]
        assert "performance" in summary["by_axis"]
    except Exception as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)

    print("PASS")