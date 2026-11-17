from collections import defaultdict
from typing import Any, Dict, List

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User

try:
    from app.models import ApiKey
except ImportError:
    from app.models import ApiKey as ApiKey


def build_api_key_inventory(session: Session) -> Dict[str, Any]:
    """Build the tenant API-key inventory without modifying any data."""
    org_rows = session.execute(
        select(Org.id, Org.name).order_by(Org.id)
    ).all()

    user_count_rows = session.execute(
        select(User.org_id, func.count(User.id)).group_by(User.org_id)
    ).all()
    user_counts = {
        org_id: int(user_count)
        for org_id, user_count in user_count_rows
        if org_id is not None
    }

    api_key_count_rows = session.execute(
        select(ApiKey.org_id, func.count(ApiKey.id)).group_by(ApiKey.org_id)
    ).all()
    api_key_counts = {
        org_id: int(api_key_count)
        for org_id, api_key_count in api_key_count_rows
        if org_id is not None
    }

    label_rows = session.execute(
        select(ApiKey.org_id, ApiKey.label)
        .where(ApiKey.label.is_not(None))
        .distinct()
        .order_by(ApiKey.org_id, ApiKey.label)
    ).all()
    labels_by_org: Dict[Any, set[str]] = defaultdict(set)
    for org_id, label in label_rows:
        if org_id is not None and label is not None:
            labels_by_org[org_id].add(str(label))

    orgs: List[Dict[str, Any]] = []
    for org_id, org_name in org_rows:
        orgs.append(
            {
                "org_id": str(org_id),
                "org_name": str(org_name),
                "user_count": user_counts.get(org_id, 0),
                "api_key_count": api_key_counts.get(org_id, 0),
                "api_key_labels": sorted(labels_by_org.get(org_id, set())),
            }
        )

    return {
        "orgs": orgs,
        "total_orgs": len(orgs),
        "total_api_keys": sum(int(org["api_key_count"]) for org in orgs),
    }


def get_api_key_inventory(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """FastAPI dependency returning the tenant API-key inventory."""
    return build_api_key_inventory(session)


if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    testing_session = sessionmaker(bind=engine)()

    testing_session.execute(
        text(
            """
            CREATE TABLE orgs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    )
    testing_session.execute(
        text(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                org_id TEXT,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    )
    testing_session.execute(
        text(
            """
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                org_id TEXT,
                key_hash TEXT NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
    )

    testing_session.execute(
        text(
            """
            INSERT INTO orgs (id, name, created_at)
            VALUES (:id, :name, :created_at)
            """
        ),
        [
            {"id": "org-1", "name": "Seeded Organization", "created_at": "2024-01-01"},
            {"id": "org-2", "name": "Empty Organization", "created_at": "2024-01-02"},
        ],
    )
    testing_session.execute(
        text(
            """
            INSERT INTO users (id, email, org_id, role, created_at)
            VALUES (:id, :email, :org_id, :role, :created_at)
            """
        ),
        [
            {
                "id": "user-1",
                "email": "one@example.com",
                "org_id": "org-1",
                "role": "member",
                "created_at": "2024-01-03",
            },
            {
                "id": "user-2",
                "email": "two@example.com",
                "org_id": "org-1",
                "role": "admin",
                "created_at": "2024-01-04",
            },
        ],
    )
    testing_session.execute(
        text(
            """
            INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
            VALUES (:id, :org_id, :key_hash, :label, :created_at)
            """
        ),
        {
            "id": "key-1",
            "org_id": "org-1",
            "key_hash": "must-not-be-exposed",
            "label": "ci",
            "created_at": "2024-01-05",
        },
    )

    result = build_api_key_inventory(testing_session)
    seeded_org = next(org for org in result["orgs"] if org["org_id"] == "org-1")

    assert result["total_orgs"] == 2
    assert seeded_org["user_count"] == 2
    assert seeded_org["api_key_count"] == 1
    assert seeded_org["api_key_labels"] == ["ci"]
    assert result["total_api_keys"] == 1
    assert "key_hash" not in repr(result)

    testing_session.close()
    engine.dispose()
    print("PASS")