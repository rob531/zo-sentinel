from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink


class TopServer(BaseModel):
    server_id: str
    link_count: int
    advisory_count: int
    max_confidence: float


class ServerConcentrationResponse(BaseModel):
    total_links: int
    distinct_servers: int
    top_servers: List[TopServer]
    links_per_server_histogram: Dict[str, int]


router = APIRouter(prefix="/vuln/links", tags=["vuln"])


def get_server_concentration(db: Session = Depends(get_session)) -> ServerConcentrationResponse:
    server_rows = (
        select(
            VulnLink.server_id.label("server_id"),
            func.count(VulnLink.id).label("link_count"),
            func.count(func.distinct(VulnLink.advisory_id)).label("advisory_count"),
            func.max(VulnLink.match_confidence).label("max_confidence"),
        )
        .where(VulnLink.server_id.is_not(None))
        .group_by(VulnLink.server_id)
        .order_by(
            func.count(VulnLink.id).desc(),
            VulnLink.server_id.asc(),
        )
    )

    rows = db.execute(server_rows).all()

    top_servers = [
        TopServer(
            server_id=str(row.server_id),
            link_count=int(row.link_count),
            advisory_count=int(row.advisory_count),
            max_confidence=float(row.max_confidence or 0.0),
        )
        for row in rows
    ]

    histogram = {"1": 0, "2": 0, "3_plus": 0}
    for row in rows:
        link_count = int(row.link_count)
        if link_count == 1:
            histogram["1"] += 1
        elif link_count == 2:
            histogram["2"] += 1
        else:
            histogram["3_plus"] += 1

    return ServerConcentrationResponse(
        total_links=sum(server.link_count for server in top_servers),
        distinct_servers=len(top_servers),
        top_servers=top_servers,
        links_per_server_histogram=histogram,
    )


@router.get(
    "/server-concentration",
    response_model=ServerConcentrationResponse,
    summary="Vulnerability link concentration by server",
)
def server_concentration(
    db: Session = Depends(get_session),
) -> ServerConcentrationResponse:
    return get_server_concentration(db)


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
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id VARCHAR,
                    server_id VARCHAR,
                    match_basis VARCHAR,
                    match_confidence FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_confidence)
                VALUES
                    (1, 'advisory-1', 'A', 'name', 0.9),
                    (2, 'advisory-2', 'A', 'name', 0.4),
                    (3, 'advisory-3', 'B', 'name', 0.7),
                    (4, 'advisory-4', NULL, 'name', 0.8)
                """
            )
        )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/vuln/links/server-concentration")
    assert response.status_code == 200
    payload = response.json()
    assert payload["distinct_servers"] == 2
    assert payload["links_per_server_histogram"]["1"] == 1
    assert payload["links_per_server_histogram"]["2"] == 1
    assert payload["top_servers"][0]["server_id"] == "A"
    assert payload["top_servers"][0]["link_count"] == 2
    assert payload["top_servers"][0]["max_confidence"] == 0.9
    print("PASS")