from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class VerdictDistributionResponse(BaseModel):
    verdict_distribution: dict[str, int]


@router.get("/api/verdicts/distribution", response_model=VerdictDistributionResponse)
def get_distribution(session: Session = Depends(get_session)) -> VerdictDistributionResponse:
    result = session.execute(
        text("SELECT verdict, COUNT(*) as count FROM McpServerRegistry WHERE verdict IS NOT NULL GROUP BY verdict")
    )
    rows = result.fetchall()
    distribution = {row[0]: row[1] for row in rows}
    return VerdictDistributionResponse(verdict_distribution=distribution)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session as SASession
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.connect() as conn:
        conn.execute(text("CREATE TABLE McpServerRegistry (verdict VARCHAR)"))
        verdicts = ["malicious", "benign", "suspicious", "unknown", "phishing", "clean"]
        for v in verdicts:
            conn.execute(text("INSERT INTO McpServerRegistry (verdict) VALUES (:v)"), {"v": v})
        conn.commit()

    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)

    from fastapi.testclient import TestClient

    client = TestClient(test_app)
    test_app.dependency_overrides[get_session] = override_get_session

    response = client.get("/api/verdicts/distribution")
    data = response.json()

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert "verdict_distribution" in data, "Missing verdict_distribution key"

    dist = data["verdict_distribution"]
    for v in verdicts:
        assert v in dist, f"Missing verdict: {v}"
        assert dist[v] == 1, f"Expected 1 for {v}, got {dist[v]}"

    assert len(dist) == 6, f"Expected 6 verdict types, got {len(dist)}"
    print("PASS")