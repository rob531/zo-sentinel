from inspect import isawaitable

from fastapi import APIRouter, Depends

from app.db import get_session
from .logic import get_verdict_tier_matrix

router = APIRouter(prefix="/api", tags=["registry"])


@router.get("/registry/verdict-tier-matrix")
async def registry_verdict_tier_matrix(session=Depends(get_session)):
    result = get_verdict_tier_matrix(session)
    if isawaitable(result):
        result = await result
    return result


def _run_self_test() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    verdict VARCHAR NOT NULL,
                    risk_tier VARCHAR NOT NULL,
                    registry_source VARCHAR NOT NULL,
                    last_assessed TIMESTAMP
                )
                """
            )
        )
        rows = [
            ("server-1", "TRUSTED_GENERAL", "LOW", "legacy"),
            ("server-2", "TRUSTED_GENERAL", "LOW", "legacy"),
            ("server-3", "TRUSTED_GENERAL", "MEDIUM", "current"),
            ("server-4", "HIGH_RISK_ISOLATED", "MEDIUM", "current"),
            ("server-5", "HIGH_RISK_ISOLATED", "HIGH", "current"),
            ("server-6", "HIGH_RISK_ISOLATED", "HIGH", "legacy"),
        ]
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, verdict, risk_tier, registry_source, last_assessed)
                VALUES (:server_id, :verdict, :risk_tier, :registry_source, :last_assessed)
                """
            ),
            [
                {
                    "server_id": server_id,
                    "verdict": verdict,
                    "risk_tier": risk_tier,
                    "registry_source": registry_source,
                    "last_assessed": datetime.now(timezone.utc),
                }
                for server_id, verdict, risk_tier, registry_source in rows
            ],
        )

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        yield session

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/registry/verdict-tier-matrix")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_servers"] == 6
            assert sum(item["count"] for item in payload["matrix"]) == 6
            assert len(payload["by_registry_source"]) == 2
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()