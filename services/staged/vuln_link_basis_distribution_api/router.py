from typing import Dict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink

try:
    from .logic import get_basis_distribution
except ImportError:
    from .logic import get_vuln_link_basis_distribution as get_basis_distribution

router = APIRouter(prefix="/api", tags=["vulnerability"])


class BasisDistributionEntry(BaseModel):
    count: int
    avg_confidence: float
    servers_covered: int


class BasisDistributionResponse(BaseModel):
    basis: Dict[str, BasisDistributionEntry]
    total_links: int
    distinct_servers: int


@router.get(
    "/vuln/links/basis",
    response_model=BasisDistributionResponse,
    summary="Vulnerability link match-basis distribution",
)
def get_vuln_link_basis_distribution(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> BasisDistributionResponse:
    return get_basis_distribution(session, limit=limit, offset=offset)


def _run_self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    match_basis TEXT NOT NULL,
                    match_value TEXT,
                    match_confidence REAL,
                    linked_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links (
                    id, advisory_id, server_id, match_basis,
                    match_value, match_confidence, linked_at
                ) VALUES
                    (1, 'ADV-1', 'SERVER-1', 'cve', 'CVE-2024-0001', 0.8, '2024-01-01'),
                    (2, 'ADV-2', 'SERVER-2', 'cve', 'CVE-2024-0002', 0.6, '2024-01-02'),
                    (3, 'ADV-3', 'SERVER-1', 'package', 'requests', 0.9, '2024-01-03'),
                    (4, 'ADV-4', 'SERVER-3', 'package', 'urllib3', 0.7, '2024-01-04')
                """
            )
        )

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/basis")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["basis"]) == 2, payload
    assert sorted(entry["count"] for entry in payload["basis"].values()) == [2, 2], payload
    assert all(
        isinstance(entry["avg_confidence"], float)
        for entry in payload["basis"].values()
    ), payload
    assert payload["total_links"] == 4, payload
    assert payload["distinct_servers"] == 3, payload

    print("PASS")


if __name__ == "__main__":
    _run_self_test()