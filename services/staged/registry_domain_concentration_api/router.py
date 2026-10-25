from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import concentration_rows

router = APIRouter(prefix="/api/registry", tags=["registry-domain-concentration"])


class HostConcentration(BaseModel):
    host: str
    server_count: int
    share: float


class DomainConcentrationResponse(BaseModel):
    total_servers: int
    distinct_hosts: int
    top_hosts: list[HostConcentration]
    concentration_top5: float


@router.get("/domain-concentration", response_model=DomainConcentrationResponse)
def get_domain_concentration(
    session: Session = Depends(get_session),
    limit: int = Query(default=25),
) -> dict:
    bounded_limit = max(1, min(200, limit))
    return concentration_rows(session, bounded_limit)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    confidence FLOAT,
                    description TEXT,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    meta JSON,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    server_id VARCHAR NOT NULL PRIMARY KEY,
                    trust_score FLOAT,
                    url TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, url)
                VALUES (:server_id, :url)
                """
            ),
            [
                {"server_id": "server-1", "url": "https://alpha.example/tools/1"},
                {"server_id": "server-2", "url": "https://alpha.example/tools/2"},
                {"server_id": "server-3", "url": "https://alpha.example/tools/3"},
                {"server_id": "server-4", "url": "https://beta.example/tools/4"},
                {"server_id": "server-5", "url": "https://beta.example/tools/5"},
            ],
        )

    testing_session = sessionmaker(bind=engine)()
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/domain-concentration")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_servers"] == 5
            assert payload["distinct_hosts"] == 2
            assert payload["top_hosts"][0]["server_count"] == 3
            assert payload["top_hosts"][0]["share"] == 0.6
            assert payload["concentration_top5"] == 1.0
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()