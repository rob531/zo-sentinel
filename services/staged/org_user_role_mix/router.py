from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User

from .logic import compute_role_mix

router = APIRouter(prefix="/api")


@router.get("/orgs/role-mix")
def get_org_user_role_mix(session: Session = Depends(get_session)):
    org_rows = session.execute(
        select(Org.id, Org.name, Org.created_at)
    ).mappings().all()
    user_rows = session.execute(
        select(User.id, User.org_id, User.role, User.email, User.created_at)
    ).mappings().all()

    orgs = {row["id"]: dict(row) for row in org_rows}
    rows = []
    populated_org_ids = set()

    for user in user_rows:
        org_id = user["org_id"]
        populated_org_ids.add(org_id)
        org = orgs.get(org_id, {})
        rows.append(
            {
                "id": user["id"],
                "user_id": user["id"],
                "org_id": org_id,
                "user_org_id": org_id,
                "role": user["role"],
                "email": user["email"],
                "created_at": user["created_at"],
                "user_created_at": user["created_at"],
                "org_name": org.get("name"),
                "org_created_at": org.get("created_at"),
            }
        )

    for org_id, org in orgs.items():
        if org_id not in populated_org_ids:
            rows.append(
                {
                    "id": None,
                    "user_id": None,
                    "org_id": org_id,
                    "user_org_id": None,
                    "role": None,
                    "email": None,
                    "created_at": None,
                    "user_created_at": None,
                    "org_name": org["name"],
                    "org_created_at": org["created_at"],
                }
            )

    return compute_role_mix(rows)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    User.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as db:
        db.add_all(
            [
                Org(id="org-1", name="Acme"),
                Org(id="org-2", name="Empty Org"),
                User(
                    id="user-1",
                    org_id="org-1",
                    role="admin",
                    email="admin1@example.com",
                    password_hash="test",
                ),
                User(
                    id="user-2",
                    org_id="org-1",
                    role="Admin",
                    email="admin2@example.com",
                    password_hash="test",
                ),
                User(
                    id="user-3",
                    org_id="org-1",
                    role="viewer",
                    email="viewer@example.com",
                    password_hash="test",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/role-mix")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_users"] == 3

    def contains_org(items, org_id):
        return any(
            item == org_id
            or (isinstance(item, dict) and item.get("org_id", item.get("id")) == org_id)
            for item in items
        )

    assert contains_org(payload["empty_orgs"], "org-2")
    assert contains_org(payload["multi_admin_orgs"], "org-1")
    print("PASS")