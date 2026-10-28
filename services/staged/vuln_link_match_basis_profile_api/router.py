from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_match_basis_profile

router = APIRouter(prefix="/api")


@router.get("/vuln/links/match-basis-profile")
def match_basis_profile(session: Session = Depends(get_session)):
    return get_match_basis_profile(session)


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
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id VARCHAR NOT NULL,
                    server_id VARCHAR NOT NULL,
                    match_basis VARCHAR NOT NULL,
                    match_value VARCHAR NOT NULL,
                    match_confidence FLOAT NOT NULL,
                    linked_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 'ADV-1', 'server-1', 'cve', 'CVE-2024-0001',
                     0.2, '2024-01-01 00:00:00'),
                    (2, 'ADV-2', 'server-2', 'cve', 'CVE-2024-0002',
                     0.9, '2024-01-02 00:00:00'),
                    (3, 'ADV-3', 'server-1', 'package', 'example-package',
                     0.8, '2024-01-03 00:00:00'),
                    (4, 'ADV-4', 'server-3', 'package', 'other-package',
                     0.7, '2024-01-04 00:00:00')
                """
            )
        )

    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/vuln/links/match-basis-profile")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_links"] == 4
    assert {basis["match_basis"] for basis in payload["bases"]} == {"cve", "package"}
    weak_links = {
        basis["match_basis"]: basis["weak_links"] for basis in payload["bases"]
    }
    assert weak_links["cve"] == 1
    assert weak_links["package"] == 0
    print("PASS")