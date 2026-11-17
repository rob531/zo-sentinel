from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink


def get_basis_distribution(
    session: Session,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(0, int(limit))
    offset = max(0, int(offset))

    rows = session.execute(
        text(
            """
            SELECT
                match_basis AS basis,
                COUNT(*) AS count,
                AVG(match_confidence) AS avg_confidence,
                COUNT(DISTINCT server_id) AS servers_covered
            FROM vuln_links
            GROUP BY match_basis
            ORDER BY COUNT(*) DESC, match_basis ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    ).mappings()

    basis: dict[str, dict[str, Any]] = {}
    for row in rows:
        average = row["avg_confidence"]
        basis[str(row["basis"])] = {
            "count": int(row["count"]),
            "avg_confidence": float(average) if average is not None else 0.0,
            "servers_covered": int(row["servers_covered"]),
        }

    totals = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS total_links,
                COUNT(DISTINCT server_id) AS distinct_servers
            FROM vuln_links
            """
        )
    ).mappings().one()

    return {
        "basis": basis,
        "total_links": int(totals["total_links"]),
        "distinct_servers": int(totals["distinct_servers"]),
    }


def get_vuln_link_basis_distribution(
    session: Session,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return get_basis_distribution(session, limit=limit, offset=offset)


def _self_test() -> None:
    from datetime import datetime

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    VulnLink.__table__.create(engine)
    with testing_session() as session:
        session.add_all(
            [
                VulnLink(
                    advisory_id=1,
                    server_id=1,
                    match_basis="version",
                    match_value="1.0",
                    match_confidence=0.9,
                    linked_at=datetime(2024, 1, 1),
                ),
                VulnLink(
                    advisory_id=1,
                    server_id=2,
                    match_basis="version",
                    match_value="1.1",
                    match_confidence=0.8,
                    linked_at=datetime(2024, 1, 2),
                ),
                VulnLink(
                    advisory_id=2,
                    server_id=1,
                    match_basis="cpe",
                    match_value="cpe:/a:example:one",
                    match_confidence=0.7,
                    linked_at=datetime(2024, 1, 3),
                ),
                VulnLink(
                    advisory_id=2,
                    server_id=2,
                    match_basis="cpe",
                    match_value="cpe:/a:example:two",
                    match_confidence=0.6,
                    linked_at=datetime(2024, 1, 4),
                ),
            ]
        )
        session.commit()

    def override_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/vuln/links/basis")
    def basis_route(session: Session = Depends(get_session)):
        return get_basis_distribution(session)

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/basis")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["basis"]) == 2
        assert max(item["count"] for item in payload["basis"].values()) == 2
        assert all(
            isinstance(item["avg_confidence"], float)
            for item in payload["basis"].values()
        )
        assert payload["total_links"] == 4
        assert payload["distinct_servers"] == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()