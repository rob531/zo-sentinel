from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


def _serialize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def audit_api_keys(session: Session) -> dict:
    statement = (
        select(
            Org.id.label("org_id"),
            Org.name.label("org_name"),
            func.count(ApiKey.id).label("key_count"),
            func.count(func.distinct(ApiKey.label)).label("distinct_labels"),
            func.min(ApiKey.created_at).label("oldest_key_created_at"),
            func.max(ApiKey.created_at).label("newest_key_created_at"),
        )
        .select_from(Org)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        .group_by(Org.id, Org.name)
        .order_by(Org.id)
    )

    rows = session.execute(statement).all()

    orgs = [
        {
            "org_id": str(row.org_id),
            "org_name": str(row.org_name),
            "key_count": int(row.key_count or 0),
            "distinct_labels": int(row.distinct_labels or 0),
            "oldest_key_created_at": _serialize_timestamp(
                row.oldest_key_created_at
            ),
            "newest_key_created_at": _serialize_timestamp(
                row.newest_key_created_at
            ),
        }
        for row in rows
    ]

    return {
        "total_keys": sum(org["key_count"] for org in orgs),
        "total_orgs": len(orgs),
        "orgs": orgs,
    }


def _self_test() -> None:
    import uuid
    from typing import List

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    class OrgKeyAudit(BaseModel):
        org_id: str
        org_name: str
        key_count: int
        distinct_labels: int
        oldest_key_created_at: str | None
        newest_key_created_at: str | None

    class ApiKeyAuditResponse(BaseModel):
        total_keys: int
        total_orgs: int
        orgs: List[OrgKeyAudit]

    def test_id(column: Any, prefix: str, number: int) -> Any:
        try:
            python_type = column.type.python_type
        except NotImplementedError:
            python_type = str

        if python_type is int:
            return number
        if python_type is uuid.UUID:
            return uuid.UUID(int=number)
        return f"{prefix}-{number}"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    now = datetime.utcnow()
    populated_org_id = test_id(Org.__table__.c.id, "org", 1)
    empty_org_id = test_id(Org.__table__.c.id, "org", 2)

    with SessionLocal() as session:
        session.add_all(
            [
                Org(
                    id=populated_org_id,
                    name="Populated Org",
                    created_at=now,
                ),
                Org(
                    id=empty_org_id,
                    name="Empty Org",
                    created_at=now,
                ),
            ]
        )
        session.flush()

        session.add_all(
            [
                ApiKey(
                    id=test_id(ApiKey.__table__.c.id, "key", 1),
                    org_id=populated_org_id,
                    key_hash="secret-hash-1",
                    label="production",
                    created_at=now,
                ),
                ApiKey(
                    id=test_id(ApiKey.__table__.c.id, "key", 2),
                    org_id=populated_org_id,
                    key_hash="secret-hash-2",
                    label="production",
                    created_at=now,
                ),
                ApiKey(
                    id=test_id(ApiKey.__table__.c.id, "key", 3),
                    org_id=populated_org_id,
                    key_hash="secret-hash-3",
                    label="staging",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/orgs/api-keys", response_model=ApiKeyAuditResponse)
    def get_org_api_key_audit(session: Session = Depends(get_session)):
        return audit_api_keys(session)

    response = TestClient(test_app).get("/api/orgs/api-keys")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_keys"] == 3
    assert payload["total_orgs"] == 2
    assert any(
        org["org_id"] == str(empty_org_id) and org["key_count"] == 0
        for org in payload["orgs"]
    )
    assert "key_hash" not in response.text

    print("PASS")


if __name__ == "__main__":
    _self_test()