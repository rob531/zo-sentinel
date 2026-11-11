from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import ApiKey as ApiKeyModel
except ImportError:
    from app.models import ApiKey as ApiKeyModel


class ApiKeyInventoryItem(BaseModel):
    id: str
    org_id: str
    org_name: Optional[str]
    label: str
    key_hash_prefix: str
    created_at: str
    age_days: int


class ApiKeyInventoryResponse(BaseModel):
    count: int
    keys: list[ApiKeyInventoryItem]
    orgs_without_keys: int


app = FastAPI(title="API Key Inventory Audit API")


def _parse_created_at(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
    else:
        raise ValueError(f"Unsupported created_at value: {value!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(created_at: object, now: Optional[datetime] = None) -> int:
    created = _parse_created_at(created_at)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    elapsed_seconds = max(0, int((current - created).total_seconds()))
    return elapsed_seconds // 86400


def _iso8601(value: object) -> str:
    return _parse_created_at(value).isoformat()


@app.get(
    "/api/security/api-keys/inventory",
    response_model=ApiKeyInventoryResponse,
)
def list_api_key_inventory(
    org_id: Optional[str] = Query(default=None),
    include_org: bool = Query(default=True),
    session: Session = Depends(get_session),
) -> ApiKeyInventoryResponse:
    parameters: dict[str, object] = {}
    org_filter = ""

    if org_id is not None:
        parameters["org_id"] = org_id
        org_filter = "WHERE CAST(o.id AS VARCHAR) = :org_id"

    keys_statement = text(
        """
        SELECT
            CAST(k.id AS VARCHAR) AS id,
            CAST(k.org_id AS VARCHAR) AS org_id,
            o.name AS org_name,
            k.label AS label,
            k.key_hash AS key_hash,
            k.created_at AS created_at
        FROM api_keys AS k
        LEFT JOIN orgs AS o
            ON o.id = k.org_id
        """
        + (
            """
            WHERE CAST(k.org_id AS VARCHAR) = :org_id
            """
            if org_id is not None
            else ""
        )
        + """
        ORDER BY k.created_at ASC, k.id ASC
        """
    )

    rows = session.execute(keys_statement, parameters).mappings().all()
    now = datetime.now(timezone.utc)
    inventory = [
        ApiKeyInventoryItem(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            org_name=str(row["org_name"]) if include_org and row["org_name"] is not None else None,
            label=str(row["label"]),
            key_hash_prefix=str(row["key_hash"])[:8],
            created_at=_iso8601(row["created_at"]),
            age_days=_age_days(row["created_at"], now),
        )
        for row in rows
    ]

    orgs_statement = text(
        """
        SELECT COUNT(*)
        FROM orgs AS o
        WHERE NOT EXISTS (
            SELECT 1
            FROM api_keys AS k
            WHERE k.org_id = o.id
        )
        """
        + (
            """
            AND CAST(o.id AS VARCHAR) = :org_id
            """
            if org_filter
            else ""
        )
    )
    orgs_without_keys = int(
        session.execute(orgs_statement, parameters).scalar_one()
    )

    return ApiKeyInventoryResponse(
        count=len(inventory),
        keys=inventory,
        orgs_without_keys=orgs_without_keys,
    )


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id VARCHAR PRIMARY KEY,
                    org_id VARCHAR NOT NULL,
                    key_hash VARCHAR NOT NULL,
                    label VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name) VALUES
                    ('org-alpha', 'Alpha Organization'),
                    ('org-beta', 'Beta Organization'),
                    ('org-empty', 'Organization Without Keys')
                """
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO api_keys
                    (id, org_id, key_hash, label, created_at)
                VALUES
                    (
                        'key-one',
                        'org-alpha',
                        '0123456789abcdef0123456789abcdef',
                        'Primary key',
                        :created_one
                    ),
                    (
                        'key-two',
                        'org-alpha',
                        'fedcba9876543210fedcba9876543210',
                        'Secondary key',
                        :created_two
                    ),
                    (
                        'key-three',
                        'org-beta',
                        'aaaabbbbccccddddeeeeffff00001111',
                        'Beta key',
                        :created_three
                    )
                """
            ),
            {
                "created_one": (now.replace(tzinfo=None) - __import__("datetime").timedelta(days=30)).isoformat(),
                "created_two": (now.replace(tzinfo=None) - __import__("datetime").timedelta(days=7)).isoformat(),
                "created_three": (now.replace(tzinfo=None) - __import__("datetime").timedelta(days=1)).isoformat(),
            },
        )

    def override_get_session() -> Iterator[Session]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/security/api-keys/inventory")
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["count"] == 3, payload
            assert len(payload["keys"]) == 3, payload
            assert any(
                item["key_hash_prefix"] == "01234567"
                for item in payload["keys"]
            ), payload
            assert all(
                item["age_days"] >= 0
                for item in payload["keys"]
            ), payload
            assert all(
                len(item["key_hash_prefix"]) <= 8
                for item in payload["keys"]
            ), payload
            assert payload["orgs_without_keys"] == 1, payload

            filtered = client.get(
                "/api/security/api-keys/inventory",
                params={"org_id": "org-alpha"},
            )
            assert filtered.status_code == 200, filtered.text
            filtered_payload = filtered.json()
            assert filtered_payload["count"] == 2, filtered_payload
            assert all(
                item["org_id"] == "org-alpha"
                for item in filtered_payload["keys"]
            ), filtered_payload

            without_org = client.get(
                "/api/security/api-keys/inventory",
                params={"include_org": "false"},
            )
            assert without_org.status_code == 200, without_org.text
            assert all(
                item["org_name"] is None
                for item in without_org.json()["keys"]
            ), without_org.json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()