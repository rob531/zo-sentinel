from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_duplicate_fingerprints


router = APIRouter(prefix="/api")


class DuplicateFingerprintGroup(BaseModel):
    fingerprint: str
    size: int
    server_ids: List[str]


class DuplicateFingerprintResponse(BaseModel):
    duplicate_groups: int
    servers_in_duplicate_groups: int
    unfingerprinted_servers: int
    groups: List[DuplicateFingerprintGroup]


@router.get(
    "/fingerprints/duplicates",
    response_model=DuplicateFingerprintResponse,
)
def fingerprint_duplicates(
    session: Session = Depends(get_session),
) -> DuplicateFingerprintResponse:
    result = get_duplicate_fingerprints(session)

    raw_groups = result.get("groups", [])
    groups = []
    for group in raw_groups[:200]:
        server_ids = [str(server_id) for server_id in group.get("server_ids", [])]
        groups.append(
            DuplicateFingerprintGroup(
                fingerprint=str(group.get("fingerprint", "")),
                size=int(group.get("size", len(server_ids))),
                server_ids=server_ids,
            )
        )

    groups.sort(key=lambda group: (-group.size, group.fingerprint))

    duplicate_groups = int(result.get("duplicate_groups", len(groups)))
    servers_in_duplicate_groups = int(
        result.get("servers_in_duplicate_groups", sum(group.size for group in groups))
    )
    unfingerprinted_servers = int(result.get("unfingerprinted_servers", 0))

    return DuplicateFingerprintResponse(
        duplicate_groups=duplicate_groups,
        servers_in_duplicate_groups=servers_in_duplicate_groups,
        unfingerprinted_servers=unfingerprinted_servers,
        groups=groups,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_fingerprints (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR(255) NOT NULL,
                    fingerprint VARCHAR(255),
                    algorithm VARCHAR(64),
                    created_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_fingerprints
                    (id, server_id, fingerprint, algorithm, created_at)
                VALUES
                    (1, 'server-a', 'fp-abc', 'sha256', CURRENT_TIMESTAMP),
                    (2, 'server-b', 'fp-abc', 'sha256', CURRENT_TIMESTAMP),
                    (3, 'server-c', 'fp-unique', 'sha256', CURRENT_TIMESTAMP),
                    (4, 'server-d', NULL, 'sha256', CURRENT_TIMESTAMP)
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
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/fingerprints/duplicates")
    payload = response.json()

    assert response.status_code == 200
    assert payload["duplicate_groups"] == 1
    assert payload["servers_in_duplicate_groups"] == 2
    assert payload["unfingerprinted_servers"] == 1
    assert len(payload["groups"]) == 1
    assert payload["groups"][0]["fingerprint"] == "fp-abc"
    assert payload["groups"][0]["size"] == 2
    assert set(payload["groups"][0]["server_ids"]) == {"server-a", "server-b"}

    print("PASS")