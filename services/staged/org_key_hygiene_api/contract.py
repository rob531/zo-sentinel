from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def _load_real_database_dependency() -> Any:
    """Load the application's real database dependency."""
    candidates = (
        ("app.db.session", "get_db"),
        ("app.db", "get_db"),
        ("app.core.database", "get_db"),
        ("app.database", "get_db"),
        ("app.database.session", "get_db"),
        ("app.data_access", "get_db"),
        ("app.db.session", "get_db_session"),
        ("app.db", "get_db_session"),
        ("app.core.database", "get_db_session"),
        ("app.database", "get_db_session"),
        ("app.data_access", "get_db_session"),
    )
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        dependency = getattr(module, attribute, None)
        if dependency is not None:
            return dependency

    for module_name in (
        "app.db.session",
        "app.db",
        "app.core.database",
        "app.database",
        "app.data_access",
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        session_factory = getattr(module, "SessionLocal", None)
        if session_factory is not None:
            def session_dependency() -> Iterator[Session]:
                session = session_factory()
                try:
                    yield session
                finally:
                    session.close()

            return session_dependency

    def unavailable_database_dependency() -> Iterator[Session]:
        raise RuntimeError("The application database dependency is not configured")
        yield  # pragma: no cover

    return unavailable_database_dependency


get_db = _load_real_database_dependency()


class OrgHygieneFinding(BaseModel):
    org_id: str
    org_name: str
    users: int
    keys: int
    unlabelled_keys: int
    keys_older_than_days: int
    inactive_users: int
    findings: list[str]


class OrgHygieneTotals(BaseModel):
    orgs: int
    users: int
    keys: int
    findings: int


class OrgHygieneResponse(BaseModel):
    days: int
    generated_at: str
    orgs: list[OrgHygieneFinding]
    totals: OrgHygieneTotals


app = FastAPI(title="Organisation API-key hygiene API")


@app.get("/api/orgs/hygiene", response_model=OrgHygieneResponse)
def organisation_hygiene(
    days: int = Query(default=90, ge=1),
    db: Session = Depends(get_db),
) -> OrgHygieneResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    organisation_rows = db.execute(
        text(
            """
            SELECT CAST(id AS TEXT) AS org_id, name
            FROM orgs
            ORDER BY CAST(id AS TEXT)
            """
        )
    ).mappings()

    user_count_rows = db.execute(
        text(
            """
            SELECT CAST(org_id AS TEXT) AS org_id, COUNT(*) AS users
            FROM users
            GROUP BY org_id
            """
        )
    ).mappings()

    key_metric_rows = db.execute(
        text(
            """
            SELECT
                CAST(org_id AS TEXT) AS org_id,
                COUNT(*) AS keys,
                COALESCE(
                    SUM(
                        CASE
                            WHEN label IS NULL OR TRIM(label) = '' THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS unlabelled_keys,
                COALESCE(
                    SUM(
                        CASE
                            WHEN created_at < :cutoff THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS keys_older_than_days
            FROM api_keys
            WHERE org_id IS NOT NULL
            GROUP BY org_id
            """
        ),
        {"cutoff": cutoff},
    ).mappings()

    inactive_user_rows = db.execute(
        text(
            """
            SELECT CAST(u.org_id AS TEXT) AS org_id, COUNT(*) AS inactive_users
            FROM users AS u
            WHERE u.org_id IS NOT NULL
              AND u.created_at < :cutoff
              AND NOT EXISTS (
                  SELECT 1
                  FROM api_keys AS k
                  WHERE k.org_id = u.org_id
                    AND k.created_at > :cutoff
              )
            GROUP BY u.org_id
            """
        ),
        {"cutoff": cutoff},
    ).mappings()

    user_counts = {
        str(row["org_id"]): int(row["users"])
        for row in user_count_rows
        if row["org_id"] is not None
    }
    key_metrics = {
        str(row["org_id"]): {
            "keys": int(row["keys"]),
            "unlabelled_keys": int(row["unlabelled_keys"]),
            "keys_older_than_days": int(row["keys_older_than_days"]),
        }
        for row in key_metric_rows
        if row["org_id"] is not None
    }
    inactive_users = {
        str(row["org_id"]): int(row["inactive_users"])
        for row in inactive_user_rows
        if row["org_id"] is not None
    }

    organisations: list[OrgHygieneFinding] = []
    for row in organisation_rows:
        org_id = str(row["org_id"])
        org_name = str(row["name"])
        users = user_counts.get(org_id, 0)
        metrics = key_metrics.get(
            org_id,
            {
                "keys": 0,
                "unlabelled_keys": 0,
                "keys_older_than_days": 0,
            },
        )
        keys = metrics["keys"]
        unlabelled_keys = metrics["unlabelled_keys"]
        stale_keys = metrics["keys_older_than_days"]
        inactive_user_count = inactive_users.get(org_id, 0)

        findings: list[str] = []
        if unlabelled_keys > 0:
            findings.append("unlabelled_key")
        if stale_keys > 0:
            findings.append("stale_key")
        if inactive_user_count > 0:
            findings.append("inactive_user")
        if keys == 0:
            findings.append("no_keys")
        if users == 0:
            findings.append("no_users")

        organisations.append(
            OrgHygieneFinding(
                org_id=org_id,
                org_name=org_name,
                users=users,
                keys=keys,
                unlabelled_keys=unlabelled_keys,
                keys_older_than_days=stale_keys,
                inactive_users=inactive_user_count,
                findings=findings,
            )
        )

    organisations.sort(key=lambda organisation: (-len(organisation.findings), organisation.org_id))

    return OrgHygieneResponse(
        days=days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        orgs=organisations,
        totals=OrgHygieneTotals(
            orgs=len(organisations),
            users=sum(organisation.users for organisation in organisations),
            keys=sum(organisation.keys for organisation in organisations),
            findings=sum(len(organisation.findings) for organisation in organisations),
        ),
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()

    orgs = Table(
        "orgs",
        metadata,
        Column("id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    users = Table(
        "users",
        metadata,
        Column("id", String, primary_key=True),
        Column("email", String, nullable=False),
        Column("org_id", String, nullable=False),
        Column("role", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    api_keys = Table(
        "api_keys",
        metadata,
        Column("id", String, primary_key=True),
        Column("org_id", String, nullable=False),
        Column("key_hash", String, nullable=False),
        Column("label", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )

    metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        connection.execute(
            orgs.insert(),
            [
                {"id": "org-a", "name": "Organisation A", "created_at": now - timedelta(days=400)},
                {"id": "org-b", "name": "Organisation B", "created_at": now - timedelta(days=50)},
            ],
        )
        connection.execute(
            users.insert(),
            [
                {
                    "id": "user-1",
                    "email": "one@example.test",
                    "org_id": "org-a",
                    "role": "admin",
                    "created_at": now - timedelta(days=250),
                },
                {
                    "id": "user-2",
                    "email": "two@example.test",
                    "org_id": "org-a",
                    "role": "member",
                    "created_at": now - timedelta(days=20),
                },
                {
                    "id": "user-3",
                    "email": "three@example.test",
                    "org_id": "org-b",
                    "role": "member",
                    "created_at": now - timedelta(days=10),
                },
            ],
        )
        connection.execute(
            api_keys.insert(),
            [
                {
                    "id": "key-1",
                    "org_id": "org-a",
                    "key_hash": "hash-one",
                    "label": None,
                    "created_at": now - timedelta(days=200),
                },
                {
                    "id": "key-2",
                    "org_id": "org-a",
                    "key_hash": "hash-two",
                    "label": "current",
                    "created_at": now - timedelta(days=5),
                },
                {
                    "id": "key-3",
                    "org_id": "org-a",
                    "key_hash": "hash-three",
                    "label": "secondary",
                    "created_at": now - timedelta(days=1),
                },
            ],
        )

    def override_get_db() -> Iterator[Session]:
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/orgs/hygiene", params={"days": 90})

            assert response.status_code == 200
            payload = response.json()
            assert len(payload["orgs"]) == 2
            assert payload["totals"]["orgs"] == 2
            assert payload["totals"]["users"] == 3
            assert payload["totals"]["keys"] == 3

            all_findings = [
                finding
                for organisation in payload["orgs"]
                for finding in organisation["findings"]
            ]
            assert "unlabelled_key" in all_findings
            assert "stale_key" in all_findings
            assert "no_keys" in all_findings
            assert "@" not in response.text

            org_a = next(
                organisation
                for organisation in payload["orgs"]
                if organisation["org_id"] == "org-a"
            )
            org_b = next(
                organisation
                for organisation in payload["orgs"]
                if organisation["org_id"] == "org-b"
            )
            assert org_a["users"] == 2
            assert org_a["keys"] == 3
            assert org_a["unlabelled_keys"] == 1
            assert org_a["keys_older_than_days"] == 1
            assert org_b["users"] == 1
            assert org_b["keys"] == 0
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()