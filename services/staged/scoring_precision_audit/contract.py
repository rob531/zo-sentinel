from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring"])


class AxisMetrics(BaseModel):
    precision: float
    recall: float
    f1_score: float


class MetricsResponse(BaseModel):
    metrics: dict[str, AxisMetrics]


@router.get("/scoring/precision/audit", response_model=MetricsResponse)
def get_precision_audit(session: Session = Depends(get_session)) -> MetricsResponse:
    sql = text("""
        SELECT
            axis_name,
            COUNT(*) as total,
            SUM(CASE WHEN ABS(score - confidence) < 0.5 THEN 1 ELSE 0 END) as correct
        FROM McpLlmAxisScore
        GROUP BY axis_name
    """)
    result = session.execute(sql)
    rows = result.fetchall()

    metrics: dict[str, AxisMetrics] = {}
    for row in rows:
        axis_name = row.axis_name
        total = row.total
        correct = row.correct

        precision = correct / total if total > 0 else 0.0
        recall = correct / total if total > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        metrics[axis_name] = AxisMetrics(
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1_score=round(f1, 4)
        )

    return MetricsResponse(metrics=metrics)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    s1 = McpServerRegistry(name="server1")
    s2 = McpServerRegistry(name="server2")
    test_session.add_all([s1, s2])
    test_session.commit()

    test_session.add_all([
        McpLlmAxisScore(server_id=s1.id, axis_name="security", score=0.8, confidence=0.9),
        McpLlmAxisScore(server_id=s1.id, axis_name="security", score=0.7, confidence=0.7),
        McpLlmAxisScore(server_id=s2.id, axis_name="security", score=0.6, confidence=0.6),
        McpLlmAxisScore(server_id=s1.id, axis_name="reliability", score=0.9, confidence=0.4),
        McpLlmAxisScore(server_id=s2.id, axis_name="reliability", score=0.5, confidence=0.5),
        McpLlmAxisScore(server_id=s2.id, axis_name="reliability", score=0.4, confidence=0.5),
    ])
    test_session.commit()

    from app.db import get_session as real_get_session
    app.dependency_overrides[real_get_session] = lambda: test_session

    client = TestClient(app)
    response = client.get("/api/scoring/precision/audit")

    if response.status_code != 200:
        print(f"FAIL: status={response.status_code}")
        sys.exit(1)

    data = response.json()
    if "metrics" not in data:
        print("FAIL: no metrics in response")
        sys.exit(1)

    metrics = data["metrics"]
    if not metrics:
        print("FAIL: metrics object is empty")
        sys.exit(1)

    required_axes = {"security", "reliability"}
    if not required_axes.issubset(metrics.keys()):
        print(f"FAIL: missing axes, got {list(metrics.keys())}")
        sys.exit(1)

    for axis_name, axis_metrics in metrics.items():
        required_fields = {"precision", "recall", "f1_score"}
        if not required_fields.issubset(axis_metrics.keys()):
            print(f"FAIL: missing fields in {axis_name}")
            sys.exit(1)

    print("PASS")