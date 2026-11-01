from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
    else:
        raise TypeError(f"Unsupported created_at value: {type(value)!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return _as_datetime(value).isoformat()
    return str(value)


def _bucket_for_age(age_days: int) -> Dict[str, Any]:
    if age_days <= 30:
        return {"bucket": "0-30", "min_days": 0, "max_days": 30}
    if age_days <= 90:
        return {"bucket": "31-90", "min_days": 31, "max_days": 90}
    if age_days <= 180:
        return {"bucket": "91-180", "min_days": 91, "max_days": 180}
    if age_days <= 365:
        return {"bucket": "181-365", "min_days": 181, "max_days": 365}
    return {"bucket": "365+", "min_days": 366, "max_days": None}


def get_api_key_age_audit(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Return an age and rotation audit for tenant API keys."""
    now = datetime.now(timezone.utc)
    result = db.execute(
        text(
            """
            SELECT
                api_keys.id,
                api_keys.org_id,
                api_keys.label,
                api_keys.created_at,
                orgs.name AS org_name
            FROM api_keys
            LEFT JOIN orgs ON orgs.id = api_keys.org_id
            ORDER BY api_keys.created_at ASC
            """
        )
    )

    keys: List[Dict[str, Any]] = []
    orgs: Dict[str, Dict[str, Any]] = {}
    bucket_counts: Dict[str, int] = {
        "0-30": 0,
        "31-90": 0,
        "91-180": 0,
        "181-365": 0,
        "365+": 0,
    }

    for row in result.mappings():
        created_at = _as_datetime(row["created_at"])
        age_days = max(0, int((now - created_at).total_seconds() // 86400))
        bucket = _bucket_for_age(age_days)
        bucket_counts[bucket["bucket"]] += 1

        key = {
            "id": str(row["id"]),
            "org_id": str(row["org_id"]),
            "org_name": row["org_name"],
            "label": row["label"],
            "created_at": _as_iso(row["created_at"]),
            "age_days": age_days,
            "bucket": bucket["bucket"],
        }
        keys.append(key)

        org_id = key["org_id"]
        if org_id not in orgs:
            orgs[org_id] = {
                "org_id": org_id,
                "org_name": key["org_name"],
                "key_count": 0,
                "oldest_created_at": key["created_at"],
                "newest_created_at": key["created_at"],
            }
        org = orgs[org_id]
        org["key_count"] += 1
        if created_at < _as_datetime(org["oldest_created_at"]):
            org["oldest_created_at"] = key["created_at"]
        if created_at > _as_datetime(org["newest_created_at"]):
            org["newest_created_at"] = key["created_at"]

    age_buckets = [
        {"bucket": "0-30", "min_days": 0, "max_days": 30, "count": bucket_counts["0-30"]},
        {"bucket": "31-90", "min_days": 31, "max_days": 90, "count": bucket_counts["31-90"]},
        {"bucket": "91-180", "min_days": 91, "max_days": 180, "count": bucket_counts["91-180"]},
        {"bucket": "181-365", "min_days": 181, "max_days": 365, "count": bucket_counts["181-365"]},
        {"bucket": "365+", "min_days": 366, "max_days": None, "count": bucket_counts["365+"]},
    ]

    return {
        "generated_at": now.isoformat(),
        "total_keys": len(keys),
        "by_org": list(orgs.values()),
        "age_buckets": age_buckets,
        "keys": keys,
    }


get_org_api_key_age_audit = get_api_key_age_audit
get_api_key_age = get_api_key_age_audit


def _self_test() -> None:
    from datetime import timedelta

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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text("INSERT INTO orgs (id, name) VALUES (:id, :name)"),
            [{"id": "org-a", "name": "Organization A"}, {"id": "org-b", "name": "Organization B"}],
        )

        now = datetime.now(timezone.utc)
        rows = [
            ("key-1", "org-a", "secret-1", "five-days", now - timedelta(days=5)),
            ("key-2", "org-a", "secret-2", "forty-days", now - timedelta(days=40)),
            ("key-3", "org-b", "secret-3", "hundred-days", now - timedelta(days=100)),
            ("key-4", "org-b", "secret-4", "four-hundred-days", now - timedelta(days=400)),
        ]
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
                VALUES (:id, :org_id, :key_hash, :label, :created_at)
                """
            ),
            [dict(zip(("id", "org_id", "key_hash", "label", "created_at"), row)) for row in rows],
        )

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/orgs/api-keys/age")
    def age_endpoint(db: Session = Depends(get_session)):
        return get_api_key_age_audit(db)

    with TestClient(test_app) as client:
        response = client.get("/orgs/api-keys/age")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 4
        assert len(payload["keys"]) == 4
        assert all(item["bucket"] for item in payload["keys"])
        assert all(item["age_days"] >= 0 for item in payload["keys"])
        oldest = next(item for item in payload["keys"] if item["id"] == "key-4")
        assert oldest["bucket"] in {"181-365", "365+"}
        body = response.text
        assert "secret-1" not in body
        assert "secret-2" not in body
        assert "secret-3" not in body
        assert "secret-4" not in body
        assert "key_hash" not in body

    print("PASS")


if __name__ == "__main__":
    _self_test()