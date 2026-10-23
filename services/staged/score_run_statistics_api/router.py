from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_score_run_statistics

router = APIRouter(prefix="/api")


@router.get("/scoring/run-statistics")
def score_run_statistics(session: Session = Depends(get_session)):
    return get_score_run_statistics(session)


if __name__ == "__main__":
    import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, McpLlmAxisScore

    # In‑memory SQLite for self‑test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    # Seed 5 rows across 3 dates
    with SessionLocal() as db:
        db.add_all(
            [
                McpLlmAxisScore(
                    adapter_sha256="a1",
                    axis_name="axis1",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=1,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.1,
                    p_danger=0.2,
                    p_top=0.7,
                    probs="{}",
                    scored_at=datetime.datetime(2023, 1, 1, 12, 0, 0),
                    server_id=1,
                ),
                McpLlmAxisScore(
                    adapter_sha256="a2",
                    axis_name="axis2",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=2,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.2,
                    p_danger=0.3,
                    p_top=0.5,
                    probs="{}",
                    scored_at=datetime.datetime(2023, 1, 2, 13, 0, 0),
                    server_id=2,
                ),
                McpLlmAxisScore(
                    adapter_sha256="a3",
                    axis_name="axis1",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=3,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.15,
                    p_danger=0.25,
                    p_top=0.6,
                    probs="{}",
                    scored_at=datetime.datetime(2023, 1, 2, 14, 0, 0),
                    server_id=1,
                ),
                McpLlmAxisScore(
                    adapter_sha256="a4",
                    axis_name="axis3",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=4,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.05,
                    p_danger=0.1,
                    p_top=0.85,
                    probs="{}",
                    scored_at=datetime.datetime(2023, 1, 3, 15, 0, 0),
                    server_id=3,
                ),
                McpLlmAxisScore(
                    adapter_sha256="a5",
                    axis_name="axis2",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=5,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.12,
                    p_danger=0.22,
                    p_top=0.66,
                    probs="{}",
                    scored_at=datetime.datetime(2023, 1, 3, 16, 0, 0),
                    server_id=2,
                ),
            ]
        )
        db.commit()

    # Dependency override for testing
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)
    resp = client.get("/api/scoring/run-statistics")
    assert resp.status_code == 200
    data = resp.json()
    assert "statistics" in data
    stats = data["statistics"]
    assert len(stats) == 3

    expected = {
        "2023-01-01": {"total_scores": 1, "unique_servers": 1, "axis_count": 1, "avg_p_top": 0.7},
        "2023-01-02": {
            "total_scores": 2,
            "unique_servers": 2,
            "axis_count": 2,
            "avg_p_top": (0.5 + 0.6) / 2,
        },
        "2023-01-03": {
            "total_scores": 2,
            "unique_servers": 2,
            "axis_count": 2,
            "avg_p_top": (0.85 + 0.66) / 2,
        },
    }

    for entry in stats:
        date = entry["date"]
        exp = expected[date]
        assert entry["total_scores"] == exp["total_scores"]
        assert entry["unique_servers"] == exp["unique_servers"]
        assert entry["axis_count"] == exp["axis_count"]
        assert abs(entry["avg_p_top"] - exp["avg_p_top"]) < 1e-6

    print("PASS")