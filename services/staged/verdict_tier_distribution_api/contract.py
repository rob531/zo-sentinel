from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


@router.get("/verdict/tier-distribution")
def get_tier_distribution(session: Session = Depends(get_session)):
    rows = (
        session.query(
            McpServerRegistry.risk_tier,
            func.count(),
            func.avg(McpServerRegistry.trust_score),
        )
        .select_from(McpServerRegistry)
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    tiers = []
    for risk_tier, count, average_trust_score in sorted(
        rows, key=lambda row: (row[0] is None, row[0] or "")
    ):
        tiers.append(
            {
                "risk_tier": risk_tier,
                "count": int(count),
                "avg_trust_score": (
                    round(float(average_trust_score), 2)
                    if average_trust_score is not None
                    else None
                ),
            }
        )

    return {
        "total": sum(tier["count"] for tier in tiers),
        "tiers": tiers,
    }


def _run_self_test():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_server_registry (
                risk_tier VARCHAR,
                trust_score FLOAT
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_server_registry (risk_tier, trust_score)
            VALUES ('low', 90.0), ('low', 80.0), ('high', 40.0)
            """
        )

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/verdict/tier-distribution")
        response.raise_for_status()
        payload = response.json()
        assert payload["total"] == 3
        assert payload["tiers"] == [
            {"risk_tier": "high", "count": 1, "avg_trust_score": 40.0},
            {"risk_tier": "low", "count": 2, "avg_trust_score": 85.0},
        ]

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()