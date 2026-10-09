from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, select, distinct
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()


class FacetsResponse(BaseModel):
    facets: dict


def get_facets(session: Session) -> dict:
    risk_tiers = [
        row[0] for row in session.execute(
            select(distinct(McpServerRegistry.risk_tier)).where(
                McpServerRegistry.risk_tier.isnot(None)
            )
        ).fetchall()
    ]

    registry_sources = [
        row[0] for row in session.execute(
            select(distinct(McpServerRegistry.registry_source)).where(
                McpServerRegistry.registry_source.isnot(None)
            )
        ).fetchall()
    ]

    verdicts = [
        row[0] for row in session.execute(
            select(distinct(McpServerRegistry.verdict)).where(
                McpServerRegistry.verdict.isnot(None)
            )
        ).fetchall()
    ]

    axis_names = [
        row[0] for row in session.execute(
            select(distinct(McpLlmAxisScore.axis_name)).where(
                McpLlmAxisScore.axis_name.isnot(None)
            )
        ).fetchall()
    ]

    score_buckets = ["0-25", "25-50", "50-75", "75-100"]

    return {
        "risk_tier": sorted(risk_tiers),
        "registry_source": sorted(registry_sources),
        "verdict": sorted(verdicts),
        "score_buckets": score_buckets,
        "axes": sorted(axis_names),
    }


def endpoint(session: Session = Depends(get_session)) -> FacetsResponse:
    return FacetsResponse(facets=get_facets(session))


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.get("/api/facets")
    def facets(session: Session = Depends(override_get_session)):
        return FacetsResponse(facets=get_facets(session))

    client = TestClient(app)

    session = TestingSessionLocal()

    s1 = McpServerRegistry(
        server_id="srv1", name="Alpha", url="https://alpha.example.com",
        risk_tier="high", registry_source="community", verdict="safe",
        confidence=0.9
    )
    s2 = McpServerRegistry(
        server_id="srv2", name="Beta", url="https://beta.example.com",
        risk_tier="low", registry_source="official", verdict="unknown",
        confidence=0.7
    )
    s3 = McpServerRegistry(
        server_id="srv3", name="Gamma", url="https://gamma.example.com",
        risk_tier="medium", registry_source="community", verdict="unsafe",
        confidence=0.5
    )
    session.add_all([s1, s2, s3])

    a1 = McpLlmAxisScore(
        id=1, server_id="srv1", axis_name="overall_risk",
        p_top=0.15, p_critical=0.1, p_danger=0.2,
        model_version="v1", decision_rule_version="r1"
    )
    a2 = McpLlmAxisScore(
        id=2, server_id="srv2", axis_name="security",
        p_top=0.85, p_critical=0.05, p_danger=0.1,
        model_version="v1", decision_rule_version="r1"
    )
    a3 = McpLlmAxisScore(
        id=3, server_id="srv3", axis_name="reliability",
        p_top=0.45, p_critical=0.15, p_danger=0.4,
        model_version="v1", decision_rule_version="r1"
    )
    session.add_all([a1, a2, a3])
    session.commit()
    session.close()

    response = client.get("/api/facets")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert "facets" in data, "Missing 'facets' in response"
    facets = data["facets"]

    assert "risk_tier" in facets, "Missing 'risk_tier' in facets"
    assert len(facets["risk_tier"]) > 0, "risk_tier has no entries"

    assert "axes" in facets, "Missing 'axes' in facets"
    assert "overall_risk" in facets["axes"], "axes does not include 'overall_risk'"

    print("PASS")