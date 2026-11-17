"""Acceptance self-test for the user organization rollup service."""
from __future__ import annotations

import sys
import uuid
from typing import Any


def _id_value(column: Any, number: int) -> Any:
    """Return an ID suitable for the model's declared column type."""
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        python_type = int

    if python_type is int:
        return number
    if python_type is uuid.UUID:
        return uuid.UUID(int=number)
    if python_type is str:
        return str(number)
    return number


def _find_breakdown(body: dict[str, Any]) -> list[dict[str, Any]]:
    for value in body.values():
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            if not value or any("role_counts" in item for item in value):
                return value
    for value in body.values():
        if isinstance(value, dict):
            nested = _find_breakdown(value)
            if nested:
                return nested
    return []


def _totals(body: dict[str, Any]) -> dict[str, Any]:
    for key in ("summary", "totals"):
        value = body.get(key)
        if isinstance(value, dict):
            return value
    return body


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, Org, User

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Org.__table__, User.__table__])
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    org_id_column = User.__table__.c.org_id
    org_one_id = _id_value(org_id_column, 1)
    org_two_id = _id_value(org_id_column, 2)
    missing_org_id = _id_value(org_id_column, 999)
    user_id_column = User.__table__.c.id
    user_ids = [
        _id_value(user_id_column, 1),
        _id_value(user_id_column, 2),
        _id_value(user_id_column, 3),
        _id_value(user_id_column, 4),
    ]

    session = TestingSession()
    session.add(Org(id=org_one_id, name="Organization One"))
    session.add(Org(id=org_two_id, name="Organization Two"))
    session.add(
        User(
            id=user_ids[0],
            email="one@example.test",
            org_id=org_one_id,
            password_hash="test",
            role="member",
        )
    )
    session.add(
        User(
            id=user_ids[1],
            email="two@example.test",
            org_id=org_one_id,
            password_hash="test",
            role="member",
        )
    )
    session.add(
        User(
            id=user_ids[2],
            email="three@example.test",
            org_id=org_two_id,
            password_hash="test",
            role="viewer",
        )
    )
    session.add(
        User(
            id=user_ids[3],
            email="four@example.test",
            org_id=missing_org_id,
            password_hash="test",
            role="viewer",
        )
    )
    session.commit()
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as client:
        response = client.get("/api/orgs/users/rollup")
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, dict), body

        totals = _totals(body)
        assert totals.get("user_count") == 4, body
        assert totals.get("unassigned_user_count") == 1, body

        breakdown = _find_breakdown(body)
        assert breakdown, body
        organization = next(
            (
                item
                for item in breakdown
                if item.get("org_name") == "Organization One"
                or str(item.get("org_id")) == str(org_one_id)
            ),
            None,
        )
        assert organization is not None, body
        assert organization["role_counts"].get("member") == 2, organization

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)