from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_url_host_concentration


class RegistrySourceCountResponse(BaseModel):
    source: str
    count: int


class RegistryHostConcentrationResponse(BaseModel):
    host: str
    count: int
    registry_sources: List[RegistrySourceCountResponse] = Field(default_factory=list)


class RegistryUrlHostConcentrationResponse(BaseModel):
    total_urls: int
    null_url_rows: int
    unique_hosts: int
    concentrated_hosts: int
    top_host_share: float
    hosts: List[RegistryHostConcentrationResponse] = Field(default_factory=list)


router = APIRouter(prefix="/api")


@router.get(
    "/registry/url-host-concentration",
    response_model=RegistryUrlHostConcentrationResponse,
)
def registry_url_host_concentration(
    limit: int = Query(default=25, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> RegistryUrlHostConcentrationResponse:
    return get_registry_url_host_concentration(session, limit=limit)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as SQLAlchemySession
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    table_name = McpServerRegistry.__tablename__

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    url TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    confidence REAL,
                    description TEXT,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    meta TEXT,
                    scan_count INTEGER,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {table_name}
                    (server_id, name, url, registry_source, trust_score)
                VALUES
                    ('seed-1', 'Seed One', 'https://github.com/orgA/mcp',
                     'github', 0.90),
                    ('seed-2', 'Seed Two', 'https://github.com/orgA/mcp',
                     'github', 0.80),
                    ('seed-3', 'Seed Three', NULL, 'registry', 0.70),
                    ('seed-4', 'Seed Four', 'https://gitlab.com/orgB/mcp',
                     'gitlab', 0.60)
                """
            )
        )

    def override_get_session():
        with SQLAlchemySession(engine) as db_session:
            yield db_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/url-host-concentration",
            params={"limit": 25},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["null_url_rows"] == 1
        assert payload["unique_hosts"] == 2
        assert payload["total_urls"] == 3
        assert payload["hosts"][0]["host"] == "github.com"
        assert payload["hosts"][0]["count"] == 2

    engine.dispose()


if __name__ == "__main__":
    _run_self_test()
    print("PASS")