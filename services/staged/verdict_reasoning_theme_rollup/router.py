from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_verdict_theme_rollup

router = APIRouter(prefix="/api")


@router.get("/verdict/themes")
def verdict_themes(
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict:
    return get_verdict_theme_rollup(session=session, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine)

    with test_session_factory() as test_session:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="theme-1",
                    verdict="malicious",
                    verdict_reasoning="auth bypass with injection evidence",
                    confidence=0.95,
                    risk_tier="critical",
                ),
                McpServerRegistry(
                    server_id="theme-2",
                    verdict="malicious",
                    verdict_reasoning="permission escalation and supply_chain tampering",
                    confidence=0.90,
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="theme-3",
                    verdict="suspicious",
                    verdict_reasoning="network anomaly and exploit indicators",
                    confidence=0.75,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="theme-4",
                    verdict="benign",
                    verdict_reasoning="no actionable findings",
                    confidence=0.20,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="theme-5",
                    verdict="malicious",
                    verdict_reasoning="auth failure and network exposure",
                    confidence=0.85,
                    risk_tier="high",
                ),
            ]
        )
        test_session.commit()

        that_app = FastAPI()
        that_app.include_router(router)

        def override_get_session():
            yield test_session

        that_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(that_app).get("/api/verdict/themes?limit=50")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert isinstance(payload.get("tiers"), dict)
        assert isinstance(payload.get("top_keywords"), list)
        assert payload.get("total_servers") == 5

        malicious_keywords = payload["tiers"].get("malicious", [])
        assert any(item["keyword"] == "auth" for item in malicious_keywords)

        top_counts = [item["count"] for item in payload["top_keywords"]]
        assert top_counts == sorted(top_counts, reverse=True)

    print("PASS")