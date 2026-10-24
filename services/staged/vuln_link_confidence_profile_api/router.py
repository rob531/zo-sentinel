from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from .logic import get_confidence_profile

router = APIRouter(prefix="/api", tags=["vulnerability-links"])


class ConfidenceBucket(BaseModel):
    bucket: str
    count: int


class MatchBasisBreakdown(BaseModel):
    match_basis: str
    count: int
    avg_confidence: float


class TopServer(BaseModel):
    server_id: str
    count: int


class ConfidenceProfileResponse(BaseModel):
    total: int
    buckets: list[ConfidenceBucket]
    by_match_basis: list[MatchBasisBreakdown]
    top_servers: list[TopServer]


@router.get(
    "/vuln/links/confidence-profile",
    response_model=ConfidenceProfileResponse,
)
def confidence_profile_endpoint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_confidence_profile(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {VulnAdvisory.__tablename__} (
                    id INTEGER PRIMARY KEY,
                    severity TEXT,
                    ecosystem TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE {VulnLink.__tablename__} (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id TEXT,
                    match_basis TEXT,
                    match_value TEXT,
                    match_confidence REAL,
                    linked_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {VulnAdvisory.__tablename__} (id, severity, ecosystem)
                VALUES (1, 'high', 'pypi'), (2, 'medium', 'npm')
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {VulnLink.__tablename__}
                    (advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 'server-1', 'package', 'requests', 0.2, CURRENT_TIMESTAMP),
                    (1, 'server-2', 'package', 'requests', 0.5, CURRENT_TIMESTAMP),
                    (2, 'server-1', 'cpe', 'cpe:/a:example/app', 0.75, CURRENT_TIMESTAMP),
                    (2, 'server-3', 'cpe', 'cpe:/a:example/app', 0.95, CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/links/confidence-profile")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert next(
            bucket["count"]
            for bucket in payload["buckets"]
            if bucket["bucket"] == "<0.4"
        ) == 1
        assert len(payload["by_match_basis"]) == 2

    print("PASS")