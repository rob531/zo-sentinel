from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink

from .logic import get_server_concentration as _get_server_concentration

router = APIRouter()


class TopServerConcentration(BaseModel):
    server_id: str
    link_count: int
    advisory_count: int
    max_confidence: float


class ServerConcentrationResponse(BaseModel):
    total_links: int
    distinct_servers: int
    top_servers: list[TopServerConcentration]
    links_per_server_histogram: dict[str, int]


@router.get(
    "/vuln/links/server-concentration",
    response_model=ServerConcentrationResponse,
)
def get_server_concentration(
    session: Session = Depends(get_session),
):
    return _get_server_concentration(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id TEXT PRIMARY KEY,
                    advisory_id TEXT,
                    linked_at DATETIME,
                    match_basis TEXT,
                    match_confidence REAL,
                    match_value TEXT,
                    server_id TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links (
                    id, advisory_id, linked_at, match_basis,
                    match_confidence, match_value, server_id
                ) VALUES (
                    :id, :advisory_id, :linked_at, :match_basis,
                    :match_confidence, :match_value, :server_id
                )
                """
            ),
            [
                {
                    "id": "1",
                    "advisory_id": "ADV-A1",
                    "linked_at": "2024-01-01T00:00:00",
                    "match_basis": "exact",
                    "match_confidence": 0.9,
                    "match_value": "match-a1",
                    "server_id": "A",
                },
                {
                    "id": "2",
                    "advisory_id": "ADV-A2",
                    "linked_at": "2024-01-02T00:00:00",
                    "match_basis": "fuzzy",
                    "match_confidence": 0.4,
                    "match_value": "match-a2",
                    "server_id": "A",
                },
                {
                    "id": "3",
                    "advisory_id": "ADV-B1",
                    "linked_at": "2024-01-03T00:00:00",
                    "match_basis": "exact",
                    "match_confidence": 0.8,
                    "match_value": "match-b1",
                    "server_id": "B",
                },
                {
                    "id": "4",
                    "advisory_id": "ADV-NULL",
                    "linked_at": "2024-01-04T00:00:00",
                    "match_basis": "exact",
                    "match_confidence": 0.7,
                    "match_value": "match-null",
                    "server_id": None,
                },
            ],
        )

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/links/server-concentration")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_links"] == 3
        assert payload["distinct_servers"] == 2
        assert payload["links_per_server_histogram"]["1"] == 1
        assert payload["links_per_server_histogram"]["2"] == 1
        assert payload["top_servers"][0]["server_id"] == "A"
        assert payload["top_servers"][0]["link_count"] == 2
        assert payload["top_servers"][0]["max_confidence"] == 0.9

    engine.dispose()
    print("PASS")