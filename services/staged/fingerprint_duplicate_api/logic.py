from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class DuplicateFingerprintGroup(BaseModel):
    fingerprint: str
    size: int
    server_ids: List[str]


class DuplicateFingerprintResponse(BaseModel):
    duplicate_groups: int
    servers_in_duplicate_groups: int
    unfingerprinted_servers: int
    groups: List[DuplicateFingerprintGroup]


_DUPLICATE_GROUPS_CTE = """
WITH duplicate_groups AS (
    SELECT
        fingerprint,
        COUNT(*) AS size
    FROM mcp_fingerprints
    WHERE fingerprint IS NOT NULL
      AND TRIM(CAST(fingerprint AS TEXT)) <> ''
    GROUP BY fingerprint
    HAVING COUNT(*) > 1
)
"""


def get_duplicate_fingerprints(session: Session) -> Dict[str, Any]:
    summary = session.execute(
        text(
            _DUPLICATE_GROUPS_CTE
            + """
            SELECT
                COUNT(*) AS duplicate_groups,
                COALESCE(SUM(size), 0) AS servers_in_duplicate_groups
            FROM duplicate_groups
            """
        )
    ).mappings().one()

    groups_result = session.execute(
        text(
            _DUPLICATE_GROUPS_CTE
            + """
            , top_groups AS (
                SELECT fingerprint, size
                FROM duplicate_groups
                ORDER BY size DESC, CAST(fingerprint AS TEXT) ASC
                LIMIT :limit
            )
            SELECT
                CAST(top_groups.fingerprint AS TEXT) AS fingerprint,
                top_groups.size AS size,
                CAST(mcp_fingerprints.server_id AS TEXT) AS server_id
            FROM top_groups
            JOIN mcp_fingerprints
              ON mcp_fingerprints.fingerprint = top_groups.fingerprint
            WHERE mcp_fingerprints.fingerprint IS NOT NULL
              AND TRIM(CAST(mcp_fingerprints.fingerprint AS TEXT)) <> ''
            ORDER BY
                top_groups.size DESC,
                CAST(top_groups.fingerprint AS TEXT) ASC,
                CAST(mcp_fingerprints.server_id AS TEXT) ASC
            """
        ),
        {"limit": 200},
    ).mappings().all()

    groups_by_fingerprint: Dict[str, Dict[str, Any]] = {}
    for row in groups_result:
        fingerprint = str(row["fingerprint"])
        group = groups_by_fingerprint.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "size": int(row["size"]),
                "server_ids": [],
            },
        )
        group["server_ids"].append(str(row["server_id"]))

    unfingerprinted = session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM mcp_fingerprints
            WHERE fingerprint IS NULL
               OR TRIM(CAST(fingerprint AS TEXT)) = ''
            """
        )
    ).scalar_one()

    return {
        "duplicate_groups": int(summary["duplicate_groups"] or 0),
        "servers_in_duplicate_groups": int(
            summary["servers_in_duplicate_groups"] or 0
        ),
        "unfingerprinted_servers": int(unfingerprinted or 0),
        "groups": list(groups_by_fingerprint.values()),
    }


find_duplicate_fingerprints = get_duplicate_fingerprints
list_duplicate_fingerprints = get_duplicate_fingerprints
get_fingerprint_duplicates = get_duplicate_fingerprints
get_fingerprint_duplicate_groups = get_duplicate_fingerprints
compute_duplicate_fingerprints = get_duplicate_fingerprints


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR NOT NULL,
                    fingerprint VARCHAR NULL,
                    algorithm VARCHAR NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_fingerprints
                    (server_id, fingerprint, algorithm, created_at)
                VALUES
                    (:server_id, :fingerprint, :algorithm, CURRENT_TIMESTAMP)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "fingerprint": "fp-abc",
                    "algorithm": "sha256",
                },
                {
                    "server_id": "server-2",
                    "fingerprint": "fp-abc",
                    "algorithm": "sha256",
                },
                {
                    "server_id": "server-3",
                    "fingerprint": "fp-unique",
                    "algorithm": "sha256",
                },
                {
                    "server_id": "server-3",
                    "fingerprint": None,
                    "algorithm": "sha256",
                },
            ],
        )

    test_app = FastAPI()

    @test_app.get(
        "/api/fingerprints/duplicates",
        response_model=DuplicateFingerprintResponse,
    )
    def duplicate_fingerprints(
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_duplicate_fingerprints(session)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/fingerprints/duplicates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["duplicate_groups"] == 1
    assert payload["servers_in_duplicate_groups"] == 2
    assert payload["unfingerprinted_servers"] == 1
    assert len(payload["groups"]) == 1
    assert payload["groups"][0]["fingerprint"] == "fp-abc"
    assert payload["groups"][0]["size"] == 2
    assert set(payload["groups"][0]["server_ids"]) == {"server-1", "server-2"}
    assert all(isinstance(value, str) for value in payload["groups"][0]["server_ids"])
    assert McpServerRegistry is not None

    print("PASS")


if __name__ == "__main__":
    _self_test()