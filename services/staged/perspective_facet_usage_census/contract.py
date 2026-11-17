from __future__ import annotations

import sys
import uuid


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, Perspective, PerspectiveSnapshot

    from .router import router

    def typed_id(column, value: int):
        try:
            python_type = column.type.python_type
        except (AttributeError, NotImplementedError):
            return value
        if python_type is uuid.UUID:
            return uuid.UUID(int=value)
        if python_type is int:
            return value
        return str(value)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False)

    perspective_one_id = typed_id(Perspective.__table__.c.id, 101)
    perspective_two_id = typed_id(Perspective.__table__.c.id, 102)
    perspective_three_id = typed_id(Perspective.__table__.c.id, 103)
    org_one_id = typed_id(Perspective.__table__.c.org_id, 201)
    org_two_id = typed_id(Perspective.__table__.c.org_id, 202)

    session = testing_session()
    try:
        session.add_all(
            [
                Perspective(
                    id=perspective_one_id,
                    org_id=org_one_id,
                    name="Perspective One",
                    description="First seeded perspective",
                    facet_filters={"capabilities": ["search"]},
                    created_by="contract-user",
                ),
                Perspective(
                    id=perspective_two_id,
                    org_id=org_one_id,
                    name="Perspective Two",
                    description="Second seeded perspective",
                    facet_filters={
                        "capabilities": ["write"],
                        "domains": ["platform"],
                    },
                    created_by="contract-user",
                ),
                Perspective(
                    id=perspective_three_id,
                    org_id=org_two_id,
                    name="Perspective Three",
                    description="Third seeded perspective",
                    facet_filters={"domains": ["security"]},
                    created_by="contract-user",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                PerspectiveSnapshot(
                    id=typed_id(PerspectiveSnapshot.__table__.c.id, 501),
                    perspective_id=typed_id(
                        PerspectiveSnapshot.__table__.c.perspective_id, 101
                    ),
                    membership={"role": "member"},
                ),
                PerspectiveSnapshot(
                    id=typed_id(PerspectiveSnapshot.__table__.c.id, 502),
                    perspective_id=typed_id(
                        PerspectiveSnapshot.__table__.c.perspective_id, 102
                    ),
                    membership={"role": "member"},
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    response = TestClient(app).get("/api/perspectives/facet-usage-census")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_perspectives"] == 3, body
    per_org = {
        str(item["org_id"]): item["count"] for item in body["per_org"]
    }
    assert per_org == {str(org_one_id): 2, str(org_two_id): 1}, body
    assert body["facet_keys"], body
    assert any(
        key in body["facet_key_counts"] and body["facet_key_counts"][key] > 0
        for key in body["facet_keys"]
    ), body
    assert [str(value) for value in body["perspectives_without_snapshots"]] == [
        str(perspective_three_id)
    ], body
    assert body.get("generated_at"), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)