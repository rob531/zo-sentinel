from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session

from .logic import get_confidence_breakdown

router = APIRouter(prefix="/api", tags=["vulnerability-links"])


class MatchBasisBreakdown(BaseModel):
    match_basis: str
    count: int
    avg_confidence: float


class ConfidenceBandBreakdown(BaseModel):
    band: str
    count: int


class EcosystemBreakdown(BaseModel):
    ecosystem: str
    count: int


class ConfidenceBreakdownResponse(BaseModel):
    total_links: int
    by_match_basis: list[MatchBasisBreakdown]
    by_confidence_band: list[ConfidenceBandBreakdown]
    top_ecosystems: list[EcosystemBreakdown]


@router.get(
    "/vuln/links/confidence",
    response_model=ConfidenceBreakdownResponse,
    status_code=200,
)
def vuln_link_confidence_breakdown(
    server_id: str | None = None,
    ecosystem: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    return get_confidence_breakdown(
        session,
        server_id=server_id,
        ecosystem=ecosystem,
    )


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        session.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE vuln_links (
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
        session.execute(
            text(
                """
                INSERT INTO vuln_advisories (id, severity, ecosystem, package)
                VALUES
                    (1, 'HIGH', 'npm', 'example-package'),
                    (2, 'LOW', 'pypi', 'another-package')
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO vuln_links (
                    id,
                    advisory_id,
                    server_id,
                    match_basis,
                    match_value,
                    match_confidence,
                    linked_at
                )
                VALUES
                    (1, 1, 'server-1', 'name', 'example-package', 0.9, '2025-01-01T00:00:00Z'),
                    (2, 2, 'server-1', 'url', 'https://example.test/advisory', 0.6, '2025-01-02T00:00:00Z'),
                    (3, 2, 'server-2', 'ecosystem', 'pypi', 0.2, '2025-01-03T00:00:00Z')
                """
            )
        )
        session.commit()

        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            yield session

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/vuln/links/confidence")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_links"] == 3, payload
        assert {row["band"]: row["count"] for row in payload["by_confidence_band"]} == {
            "high": 1,
            "medium": 1,
            "low": 1,
        }, payload
        assert any(
            row["match_basis"] == "name"
            and row["count"] == 1
            and row["avg_confidence"] == 0.9
            for row in payload["by_match_basis"]
        ), payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()