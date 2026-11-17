from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


def get_api_key_usage_rollup(
    session: Session,
    months: int = 6,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a read-only monthly API-key issuance rollup."""
    if months < 1:
        raise ValueError("months must be at least 1")

    month = func.substr(cast(ApiKey.created_at, String), 1, 7)

    statement = (
        select(
            month.label("month"),
            Org.id.label("org_id"),
            Org.name.label("org_name"),
            func.count(ApiKey.id).label("key_count"),
        )
        .select_from(ApiKey)
        .join(Org, ApiKey.org_id == Org.id)
        .group_by(month, Org.id, Org.name)
        .order_by(month.desc(), Org.name.asc(), Org.id.asc())
    )

    if org_id is not None:
        statement = statement.where(ApiKey.org_id == org_id)

    aggregated = [
        {
            "month": row.month,
            "org_id": row.org_id,
            "org_name": row.org_name,
            "key_count": int(row.key_count),
        }
        for row in session.execute(statement)
    ]

    available_months = sorted(
        {row["month"] for row in aggregated},
        reverse=True,
    )
    selected_months = set(available_months[:months])

    rows = [row for row in aggregated if row["month"] in selected_months]
    rows.sort(key=lambda row: (row["month"], row["org_name"], str(row["org_id"])), reverse=True)

    return {
        "months": months,
        "rows": rows,
        "totals": {
            "orgs_with_keys": len({row["org_id"] for row in rows}),
            "keys": sum(row["key_count"] for row in rows),
        },
    }


def get_key_usage_rollup(
    session: Session,
    months: int = 6,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_api_key_usage_rollup(session, months=months, org_id=org_id)


def get_api_key_rollup(
    session: Session,
    months: int = 6,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_api_key_usage_rollup(session, months=months, org_id=org_id)


def get_rollup(
    session: Session,
    months: int = 6,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_api_key_usage_rollup(session, months=months, org_id=org_id)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    Org.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)

    now = datetime.utcnow()
    current_month = now.replace(day=15)
    previous_month = (now.replace(day=1) - timedelta(days=1)).replace(day=15)

    with TestSession() as session:
        session.add_all(
            [
                Org(id="org-a", name="Alpha", created_at=current_month),
                Org(id="org-b", name="Beta", created_at=current_month),
                Org(id="org-c", name="Gamma", created_at=previous_month),
            ]
        )
        session.flush()
        session.add_all(
            [
                ApiKey(
                    id="key-a1",
                    org_id="org-a",
                    key_hash="a" * 64,
                    label="alpha-one",
                    created_at=current_month,
                ),
                ApiKey(
                    id="key-a2",
                    org_id="org-a",
                    key_hash="b" * 64,
                    label="alpha-two",
                    created_at=previous_month,
                ),
                ApiKey(
                    id="key-b1",
                    org_id="org-b",
                    key_hash="c" * 64,
                    label="beta-one",
                    created_at=current_month,
                ),
                ApiKey(
                    id="key-c1",
                    org_id="org-c",
                    key_hash="d" * 64,
                    label="gamma-one",
                    created_at=previous_month,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    @test_app.get("/api/orgs/keys/rollup")
    def rollup_endpoint(
        months: int = 6,
        org_id: Optional[str] = None,
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_api_key_usage_rollup(session, months=months, org_id=org_id)

    client = TestClient(test_app)

    response = client.get("/api/orgs/keys/rollup", params={"months": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["months"] == 2
    assert sum(row["key_count"] for row in payload["rows"]) == 4
    assert payload["totals"] == {"orgs_with_keys": 3, "keys": 4}
    assert all("key_hash" not in row for row in payload["rows"])
    assert all(hash_value not in response.text for hash_value in ("a" * 64, "b" * 64, "c" * 64, "d" * 64))

    latest_response = client.get("/api/orgs/keys/rollup", params={"months": 1})
    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload["months"] == 1
    assert len({row["month"] for row in latest_payload["rows"]}) == 1
    assert sum(row["key_count"] for row in latest_payload["rows"]) == 2

    filtered_response = client.get(
        "/api/orgs/keys/rollup",
        params={"months": 2, "org_id": "org-a"},
    )
    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["totals"] == {"orgs_with_keys": 1, "keys": 2}
    assert all(row["org_id"] == "org-a" for row in filtered_payload["rows"])

    print("PASS")