# services/staged/score_run_statistics_api/logic.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Generator, List, Dict, Any

from app.db import get_session, Base
from app.models import McpLlmAxisScore

router = APIRouter()


@router.get("/api/scoring/run-statistics")
def get_run_statistics(db: Session = Depends(get_session)) -> Dict[str, List[Dict[str, Any]]]:
    stmt = (
        select(
            func.date_trunc("day", McpLlmAxisScore.scored_at).label("day"),
            func.count().label("total_scores"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("unique_servers"),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("axis_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .group_by("day")
        .order_by("day")
    )
    result = db.execute(stmt)
    rows = result.fetchall()

    statistics = [
        {
            "date": row.day.date().isoformat(),
            "total_scores": row.total_scores,
            "unique_servers": row.unique_servers,
            "axis_count": row.axis_count,
            "avg_p_top": float(row.avg_p_top) if row.avg_p_top is not None else None,
        }
        for row in rows
    ]

    return {"statistics": statistics}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for testing only)
    # ------------------------------------------------------------------- #
    TEST_ENGINE = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE)

    Base.metadata.create_all(bind=TEST_ENGINE)

    def get_test_session() -> Generator[Session, None, None]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Populate test data (5 rows across 3 distinct dates)
    # ------------------------------------------------------------------- #
    test_rows = [
        McpLlmAxisScore(
            adapter_sha256="a1",
            axis_name="axis1",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=1,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.8,
            probs="{}",
            scored_at=datetime.datetime(2023, 1, 1, 10, 0, 0),
            server_id=1,
        ),
        McpLlmAxisScore(
            adapter_sha256="a2",
            axis_name="axis2",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=2,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.6,
            probs="{}",
            scored_at=datetime.datetime(2023, 1, 1, 12, 0, 0),
            server_id=2,
        ),
        McpLlmAxisScore(
            adapter_sha256="a3",
            axis_name="axis1",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=3,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.9,
            probs="{}",
            scored_at=datetime.datetime(2023, 1, 2, 9, 0, 0),
            server_id=1,
        ),
        McpLlmAxisScore(
            adapter_sha256="a4",
            axis_name="axis3",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=4,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.7,
            probs="{}",
            scored_at=datetime.datetime(2023, 1, 3, 15, 0, 0),
            server_id=3,
        ),
        McpLlmAxisScore(
            adapter_sha256="a5",
            axis_name="axis4",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=5,
            label="lbl",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.5,
            probs="{}",
            scored_at=datetime.datetime(2023, 1, 3, 18, 0, 0),
            server_id=4,
        ),
    ]

    with TestSessionLocal() as db:
        db.add_all(test_rows)
        db.commit()

    # ------------------------------------------------------------------- #
    # FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    response = client.get("/api/scoring/run-statistics")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    stats = data.get("statistics", [])
    assert len(stats) == 3, f"Expected 3 dates, got {len(stats)}"

    # Expected aggregates per date
    expected = {
        "2023-01-01": {"total_scores": 2, "unique_servers": 2, "axis_count": 2, "avg_p_top": 0.7},
        "2023-01-02": {"total_scores": 1, "unique_servers": 1, "axis_count": 1, "avg_p_top": 0.9},
        "2023-01-03": {"total_scores": 2, "unique_servers": 2, "axis_count": 2, "avg_p_top": 0.6},
    }

    for entry in stats:
        date = entry["date"]
        exp = expected[date]
        assert entry["total_scores"] == exp["total_scores"]
        assert entry["unique_servers"] == exp["unique_servers"]
        assert entry["axis_count"] == exp["axis_count"]
        # allow small floating‑point differences
        assert abs(entry["avg_p_top"] - exp["avg_p_top"]) < 1e-6

    print("PASS")