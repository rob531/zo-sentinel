from __future__ import annotations

import inspect
import sys
from collections.abc import Mapping


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, User

    from . import logic
    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            User(
                id="age-user-1",
                email="age1@example.test",
                org_id="age-org-a",
                password_hash="test",
                role="admin",
            ),
            User(
                id="age-user-2",
                email="age2@example.test",
                org_id="age-org-a",
                password_hash="test",
                role="member",
            ),
            User(
                id="age-user-3",
                email="age3@example.test",
                org_id="age-org-b",
                password_hash="test",
                role="viewer",
            ),
            User(
                id="age-user-4",
                email="age4@example.test",
                org_id="age-org-a",
                password_hash="test",
                role="member",
            ),
            User(
                id="age-user-5",
                email="age5@example.test",
                org_id="age-org-b",
                password_hash="test",
                role="admin",
            ),
            User(
                id="age-user-6",
                email="age6@example.test",
                org_id="age-org-c",
                password_hash="test",
                role="viewer",
            ),
            User(
                id="age-user-7",
                email="age7@example.test",
                org_id="age-org-b",
                password_hash="test",
                role="member",
            ),
            User(
                id="age-user-8",
                email="age8@example.test",
                org_id="age-org-c",
                password_hash="test",
                role="admin",
            ),
        ]
    )
    session.commit()

    ages = [1, 3, 7, 8, 30, 31, 90, 366]
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    for user, age in zip(session.query(User).order_by(User.id).all(), ages):
        user.created_at = now - timedelta(days=age)
    session.commit()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    def _payload(value):
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        raise AssertionError(f"unexpected logic result: {value!r}")

    candidates = []
    for name in (
        "get_age_cohorts",
        "get_age_cohort_data",
        "get_age_cohort_report",
        "get_user_age_cohorts",
        "query_age_cohorts",
    ):
        candidate = getattr(logic, name, None)
        if callable(candidate):
            candidates.append(candidate)

    for route in router.routes:
        endpoint = getattr(route, "endpoint", None)
        for value in getattr(endpoint, "__globals__", {}).values():
            if (
                callable(value)
                and getattr(value, "__module__", None) == logic.__name__
                and value not in candidates
            ):
                candidates.append(value)

    if not candidates:
        raise AssertionError("no age-cohort logic function found")

    days = 400
    result = None
    errors = []
    for candidate in candidates:
        try:
            signature = inspect.signature(candidate)
            parameters = signature.parameters
            kwargs = {}
            if "days" in parameters:
                kwargs["days"] = days
            for name in ("session", "db"):
                if name in parameters:
                    kwargs[name] = session
                    break
            if len(parameters) >= 2 and not kwargs:
                result = candidate(session, days)
            elif kwargs:
                result = candidate(**kwargs)
            else:
                result = candidate(session)
            if result is not None:
                break
        except (TypeError, ValueError, AttributeError) as exc:
            errors.append(f"{getattr(candidate, '__name__', candidate)!r}: {exc}")

    if result is None:
        raise AssertionError("unable to invoke age-cohort logic: " + "; ".join(errors))

    payload = _payload(result)
    cohorts = payload.get("cohorts")
    assert isinstance(cohorts, list), payload
    cohort_map = {item["bucket"]: item["count"] for item in cohorts}
    expected_buckets = {"0-7", "8-30", "31-90", "91-365", "365+"}
    assert set(cohort_map) == expected_buckets, cohort_map
    assert sum(cohort_map.values()) == 8, cohort_map
    assert payload.get("total") == 8, payload

    by_role = payload.get("by_role")
    assert isinstance(by_role, list), payload
    role_map = {item["role"]: item["count"] for item in by_role}
    assert {"admin", "member", "viewer"}.issubset(role_map), role_map
    assert sum(role_map.values()) == 8, role_map

    by_org = payload.get("by_org")
    assert isinstance(by_org, list), payload
    org_map = {item["org_id"]: item["count"] for item in by_org}
    assert {"age-org-a", "age-org-b", "age-org-c"}.issubset(org_map), org_map
    assert sum(org_map.values()) == 8, org_map

    client = TestClient(app)
    response = client.get(f"/api/users/age-cohort?days={days}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "cohorts" in body, body
    assert body["total"] == 8, body
    assert sum(item["count"] for item in body["cohorts"]) == 8, body

    session.close()
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)