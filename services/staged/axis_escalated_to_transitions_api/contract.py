import datetime
from collections import Counter, defaultdict
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class TransitionBucket(BaseModel):
    axis_name: str
    escalated_to: str
    count: int
    server_count: int
    avg_p_top: float
    avg_p_critical: float
    avg_p_danger: float
    top_label: Optional[str] = None


class EscalatedTransitionsResponse(BaseModel):
    days: int
    total_escalated: int
    transitions: List[TransitionBucket]


def _aggregate(rows: List[McpLlmAxisScore]) -> Tuple[int, List[TransitionBucket]]:
    total = len(rows)
    groups = defaultdict(list)
    for r in rows:
        key = (r.axis_name, r.escalated_to)
        groups[key].append(r)

    buckets: List[TransitionBucket] = []
    for (axis_name, escalated_to), items in groups.items():
        count = len(items)
        server_ids = {i.server_id for i in items}
        avg_p_top = sum(i.p_top or 0 for i in items) / count
        avg_p_critical = sum(i.p_critical or 0 for i in items) / count
        avg_p_danger = sum(i.p_danger or 0 for i in items) / count
        labels = [i.label for i in items if i.label is not None]
        top_label = Counter(labels).most_common(1)[0][0] if labels else None

        buckets.append(
            TransitionBucket(
                axis_name=axis_name,
                escalated_to=escalated_to,
                count=count,
                server_count=len(server_ids),
                avg_p_top=avg_p_top,
                avg_p_critical=avg_p_critical,
                avg_p_danger=avg_p_danger,
                top_label=top_label,
            )
        )
    return total, buckets


@router.get(
    "/axis/escalated-transitions",
    response_model=EscalatedTransitionsResponse,
    name="axis_escalated_to_transitions",
)
def get_escalated_transitions(
    days: int = Query(30, ge=1, le=365),
    axis_name: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    q = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.escalated.is_(True),
        McpLlmAxisScore.scored_at >= cutoff,
    )
    if axis_name:
        q = q.filter(McpLlmAxisScore.axis_name == axis_name)

    rows = q.all()
    total, buckets = _aggregate(rows)

    return EscalatedTransitionsResponse(
        days=days,
        total_escalated=total,
        transitions=buckets,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite with StaticPool (shared across sessions)
    ENGINE = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool").pool.StaticPool,
    )
    SessionLocal = sessionmaker(bind=ENGINE)

    # Create tables
    from app.models import Base  # type: ignore

    Base.metadata.create_all(bind=ENGINE)

    # Helper to provide a session for the FastAPI dependency override
    def get_test_session() -> Session:
        return SessionLocal()

    # Seed data
    now = datetime.datetime.utcnow()
    test_rows = [
        # overall_risk, escalated HIGH_RISK_ISOLATED, server 1
        McpLlmAxisScore(
            id=1,
            server_id=1,
            axis_name="overall_risk",
            label="high",
            label_index=2,
            probs='{}',
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            escalated=True,
            escalated_to="HIGH_RISK_ISOLATED",
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now,
        ),
        # overall_risk, escalated HIGH_RISK_ISOLATED, server 2
        McpLlmAxisScore(
            id=2,
            server_id=2,
            axis_name="overall_risk",
            label="high",
            label_index=2,
            probs='{}',
            p_top=0.85,
            p_critical=0.75,
            p_danger=0.65,
            escalated=True,
            escalated_to="HIGH_RISK_ISOLATED",
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now,
        ),
        # auth_strength, escalated CAUTION_LIMITED, server 3
        McpLlmAxisScore(
            id=3,
            server_id=3,
            axis_name="auth_strength",
            label="low",
            label_index=0,
            probs='{}',
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            escalated=True,
            escalated_to="CAUTION_LIMITED",
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now,
        ),
        # not escalated, should be ignored
        McpLlmAxisScore(
            id=4,
            server_id=4,
            axis_name="overall_risk",
            label="medium",
            label_index=1,
            probs='{}',
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now,
        ),
    ]

    sess = SessionLocal()
    sess.add_all(test_rows)
    sess.commit()
    sess.close()

    # Build FastAPI app for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/axis/escalated-transitions")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total_escalated"] == 3, "total_escalated mismatch"
    assert len(data["transitions"]) == 2, "expected two transition buckets"

    # Find overall_risk bucket
    overall = next(
        (b for b in data["transitions"] if b["axis_name"] == "overall_risk"), None
    )
    assert overall is not None, "overall_risk bucket missing"
    assert overall["count"] == 2, "overall_risk count mismatch"
    assert overall["server_count"] == 2, "overall_risk server_count mismatch"

    print("PASS")