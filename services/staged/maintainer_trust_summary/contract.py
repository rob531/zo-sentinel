"""
services.staged.maintainer_trust_summary.contract
"""

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

# Real application data layer
from app.db import get_session, Base
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class SummaryResponse(BaseModel):
    axis: str
    total_servers: int
    avg_p_top: float
    critical_count: int
    danger_count: int
    unknown_count: int


def _compute_summary(rows):
    if not rows:
        return {
            "axis": "maintainer_trust",
            "total_servers": 0,
            "avg_p_top": 0.0,
            "critical_count": 0,
            "danger_count": 0,
            "unknown_count": 0,
        }

    total_servers = len({r.server_id for r in rows})
    avg_p_top = sum(r.p_top for r in rows) / len(rows)

    critical_count = sum(
        1
        for r in rows
        if r.p_critical >= r.p_top and r.p_critical >= r.p_danger
    )
    danger_count = sum(
        1
        for r in rows
        if r.p_danger >= r.p_top and r.p_danger >= r.p_critical
    )
    unknown_count = len(rows) - critical_count - danger_count

    return {
        "axis": "maintainer_trust",
        "total_servers": total_servers,
        "avg_p_top": avg_p_top,
        "critical_count": critical_count,
        "danger_count": danger_count,
        "unknown_count": unknown_count,
    }


@router.get(
    "/axis/maintainer_trust_summary",
    response_model=SummaryResponse,
    name="maintainer_trust_summary",
)
def get_maintainer_trust_summary(session: Session = Depends(get_session)):
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .all()
    )
    return _compute_summary(rows)


# --------------------------------------------------------------------------- #
# Self‑test (runnable with `python -m services.staged.maintainer_trust_summary.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real schema
    # ------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine)

    Base.metadata.create_all(bind=test_engine)

    # ------------------------------------------------------------------- #
    # Seed deterministic test data (5 distinct servers)
    # ------------------------------------------------------------------- #
    test_session = TestSessionLocal()
    seed_rows = [
        McpLlmAxisScore(
            server_id=1,
            axis_name="maintainer_trust",
            p_top=0.10,
            p_critical=0.60,
            p_danger=0.20,
            adapter_sha256="a1",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=1,
            label="label1",
            label_index=0,
            model_version="m1",
            probs=None,
            scored_at=None,
        ),
        McpLlmAxisScore(
            server_id=2,
            axis_name="maintainer_trust",
            p_top=0.20,
            p_critical=0.10,
            p_danger=0.70,
            adapter_sha256="a2",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=2,
            label="label2",
            label_index=0,
            model_version="m1",
            probs=None,
            scored_at=None,
        ),
        McpLlmAxisScore(
            server_id=3,
            axis_name="maintainer_trust",
            p_top=0.30,
            p_critical=0.20,
            p_danger=0.10,
            adapter_sha256="a3",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=3,
            label="label3",
            label_index=0,
            model_version="m1",
            probs=None,
            scored_at=None,
        ),
        McpLlmAxisScore(
            server_id=4,
            axis_name="maintainer_trust",
            p_top=0.40,
            p_critical=0.30,
            p_danger=0.20,
            adapter_sha256="a4",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=4,
            label="label4",
            label_index=0,
            model_version="m1",
            probs=None,
            scored_at=None,
        ),
        McpLlmAxisScore(
            server_id=5,
            axis_name="maintainer_trust",
            p_top=0.50,
            p_critical=0.40,
            p_danger=0.30,
            adapter_sha256="a5",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=5,
            label="label5",
            label_index=0,
            model_version="m1",
            probs=None,
            scored_at=None,
        ),
    ]
    test_session.add_all(seed_rows)
    test_session.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute request and validate response
    # ------------------------------------------------------------------- #
    response = client.get("/api/axis/maintainer_trust_summary")
    if response.status_code != 200:
        print(f"FAIL: unexpected status {response.status_code}", file=sys.stderr)
        sys.exit(1)

    data = response.json()
    expected_avg = sum([0.10, 0.20, 0.30, 0.40, 0.50]) / 5
    if abs(data["avg_p_top"] - expected_avg) > 1e-6:
        print(
            f"FAIL: avg_p_top {data['avg_p_top']} != expected {expected_avg}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("PASS")
    sys.exit(0)