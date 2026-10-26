from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_name_collisions


router = APIRouter(prefix="/api", tags=["registry"])


class NameCollision(BaseModel):
    name: str
    server_count: int
    registry_sources: list[str]
    risk_tiers: list[str]
    server_ids: list[str]


class NameCollisionResponse(BaseModel):
    collision_count: int
    colliding_names: list[NameCollision]


@router.get(
    "/registry/name-collisions",
    response_model=NameCollisionResponse,
)
def registry_name_collisions(
    session: Session = Depends(get_session),
) -> NameCollisionResponse:
    result = get_name_collisions(session)

    if isinstance(result, dict):
        result = dict(result)
        result["colliding_names"] = list(result.get("colliding_names", []))[:200]
        return NameCollisionResponse(**result)

    if isinstance(result, NameCollisionResponse):
        result.colliding_names = result.colliding_names[:200]
        return result

    return NameCollisionResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    registry_source VARCHAR,
                    url VARCHAR,
                    trust_score FLOAT,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    first_seen DATETIME,
                    last_seen DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    registry_source,
                    url,
                    trust_score,
                    risk_tier,
                    verdict,
                    first_seen,
                    last_seen
                ) VALUES
                    ('server-1', 'Shared Server', 'registry-a', 'https://one.example', 0.8, 'low', 'trusted', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('server-2', 'shared server', 'registry-b', 'https://two.example', 0.6, 'medium', 'review', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('server-3', 'Unique Server', 'registry-a', 'https://three.example', 0.9, 'low', 'trusted', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('server-4', 'Another Unique Server', 'registry-b', 'https://four.example', 0.7, 'high', 'risky', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/name-collisions")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["collision_count"] == 1, payload
        assert len(payload["colliding_names"]) == 1, payload

        collision = payload["colliding_names"][0]
        assert collision["name"] == "shared server", payload
        assert collision["server_count"] == 2, payload
        assert set(collision["server_ids"]) == {"server-1", "server-2"}, payload
        assert set(collision["registry_sources"]) == {"registry-a", "registry-b"}, payload
        assert set(collision["risk_tiers"]) == {"low", "medium"}, payload
        assert all(item["name"] != "Unique Server" for item in payload["colliding_names"])
        assert all(item["name"] != "Another Unique Server" for item in payload["colliding_names"])

    print("PASS")