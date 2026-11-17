from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink

from .logic import compute_match_basis_mix

router = APIRouter()


@router.get("/api/vuln/match-basis")
def get_match_basis_mix(session: Session = Depends(get_session)) -> dict:
    rows = session.execute(
        select(
            VulnLink.id,
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_value,
            VulnLink.match_confidence,
            VulnLink.linked_at,
        )
    ).mappings().all()
    return compute_match_basis_mix([dict(row) for row in rows])


if __name__ == "__main__":
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER NOT NULL,
                    server_id INTEGER NOT NULL,
                    match_basis TEXT,
                    match_value TEXT NOT NULL,
                    match_confidence REAL NOT NULL,
                    linked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (advisory_id, server_id, match_basis, match_value, match_confidence)
                VALUES
                    (1, 1, 'package', 'requests', 0.9),
                    (2, 2, 'package', 'urllib3', 0.2),
                    (3, 3, 'identity', 'github:example/server', 0.6),
                    (4, 4, NULL, 'unknown', 0.4)
                """
            )
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/vuln/match-basis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4

    by_basis = {item["basis"]: item for item in payload["by_basis"]}
    assert by_basis["package"]["count"] == 2
    assert by_basis["package"]["avg_confidence"] == 0.55
    assert by_basis["package"]["low_confidence_count"] == 1
    assert by_basis["identity"]["count"] == 1
    assert by_basis["unknown"]["count"] == 1
    assert payload["low_confidence_total"] == 2

    print("PASS")