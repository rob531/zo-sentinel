from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


def _normalise_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_since(value: Optional[datetime], now: datetime) -> Optional[int]:
    if value is None:
        return None
    delta = now - _as_utc(value)
    return max(0, int(delta.total_seconds() // 86400))


def list_indicators(session: Session = Depends(get_session)) -> Dict[str, Any]:
    org_rows = session.execute(
        select(Org.id, Org.name).order_by(Org.id)
    ).all()
    key_rows = session.execute(
        select(ApiKey.id, ApiKey.org_id, ApiKey.label, ApiKey.created_at)
    ).all()
    user_rows = session.execute(select(User.id, User.org_id)).all()

    org_ids = {row.id for row in org_rows}
    keys_by_org: Dict[Any, List[Any]] = {}
    users_by_org: Dict[Any, int] = {}
    orphan_key_count = 0

    for key_id, org_id, label, created_at in key_rows:
        if org_id not in org_ids:
            orphan_key_count += 1
        keys_by_org.setdefault(org_id, []).append((key_id, label, created_at))

    for user_id, org_id in user_rows:
        users_by_org[org_id] = users_by_org.get(org_id, 0) + 1

    now = datetime.now(timezone.utc)
    organisations: List[Dict[str, Any]] = []
    orgs_without_keys = 0

    for org_id, name in org_rows:
        org_keys = keys_by_org.get(org_id, [])
        key_count = len(org_keys)

        if key_count == 0:
            orgs_without_keys += 1

        labels = {label for _, label, _ in org_keys if label is not None}
        timestamps = [
            timestamp
            for timestamp in (
                _normalise_datetime(created_at) for _, _, created_at in org_keys
            )
            if timestamp is not None
        ]
        oldest_key_created_at = min(timestamps) if timestamps else None
        newest_key_created_at = max(timestamps) if timestamps else None

        organisations.append(
            {
                "org_id": org_id,
                "name": name,
                "key_count": key_count,
                "distinct_labels": len(labels),
                "oldest_key_created_at": oldest_key_created_at,
                "days_since_newest_key": _days_since(newest_key_created_at, now),
                "user_count": users_by_org.get(org_id, 0),
                "orphan_key_count": orphan_key_count,
            }
        )

    return {
        "orgs": organisations,
        "totals": {
            "org_count": len(org_rows),
            "total_keys": len(key_rows),
            "orgs_without_keys": orgs_without_keys,
            "orphan_keys": orphan_key_count,
        },
        "generated_at": now,
    }


def get_api_key_hygiene(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return list_indicators(session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE orgs ("
                "id INTEGER PRIMARY KEY, "
                "name VARCHAR NOT NULL, "
                "created_at DATETIME NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE api_keys ("
                "id INTEGER PRIMARY KEY, "
                "org_id INTEGER, "
                "key_hash VARCHAR NOT NULL, "
                "label VARCHAR, "
                "created_at DATETIME NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, "
                "org_id INTEGER, "
                "role VARCHAR, "
                "created_at DATETIME NOT NULL"
                ")"
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text("INSERT INTO orgs (id, name, created_at) VALUES (1, 'Alpha', :now)"),
            {"now": now},
        )
        connection.execute(
            text("INSERT INTO orgs (id, name, created_at) VALUES (2, 'Beta', :now)"),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO api_keys "
                "(id, org_id, key_hash, label, created_at) "
                "VALUES (1, 1, 'hash-1', 'Production', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO api_keys "
                "(id, org_id, key_hash, label, created_at) "
                "VALUES (2, 1, 'hash-2', 'Development', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO api_keys "
                "(id, org_id, key_hash, label, created_at) "
                "VALUES (3, 999, 'hash-3', 'Orphaned', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, org_id, role, created_at) "
                "VALUES (1, 1, 'admin', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, org_id, role, created_at) "
                "VALUES (2, 2, 'member', :now)"
            ),
            {"now": now},
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/orgs/api-key-hygiene")
    def api_key_hygiene(session: Session = Depends(get_session)):
        return list_indicators(session)

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/api-key-hygiene")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["org_count"] == 2
        assert payload["totals"]["orphan_keys"] == 1
        assert payload["totals"]["total_keys"] == 3

    print("PASS")