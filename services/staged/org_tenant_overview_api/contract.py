from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User


class OrgSummary(BaseModel):
    id: str
    name: str
    created_at: datetime


class UserSummary(BaseModel):
    total: int
    by_role: Dict[str, int]


class ApiKeySummary(BaseModel):
    total: int
    labels: List[str]


class OrgTenantOverviewResponse(BaseModel):
    org: OrgSummary
    users: UserSummary
    api_keys: ApiKeySummary
    generated_at: datetime


router = APIRouter()


def _coerce_org_id(value: str):
    try:
        python_type = Org.id.type.python_type
    except (AttributeError, NotImplementedError):
        return value

    try:
        if python_type is int:
            return int(value)
        if issubclass(python_type, UUID):
            return UUID(value)
    except (TypeError, ValueError):
        pass
    return value


def _get_org_overview(org_id: str, db: Session) -> OrgTenantOverviewResponse:
    filter_org_id = _coerce_org_id(org_id)

    org = db.execute(
        select(Org).where(Org.id == filter_org_id)
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    user_roles = db.execute(
        select(User.role)
        .where(User.org_id == filter_org_id)
        .order_by(User.role)
    ).scalars().all()
    role_counts = Counter(str(role) for role in user_roles if role is not None)

    api_key_labels = db.execute(
        select(ApiKey.label)
        .where(ApiKey.org_id == filter_org_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    ).scalars().all()

    return OrgTenantOverviewResponse(
        org=OrgSummary(
            id=str(org.id),
            name=org.name,
            created_at=org.created_at,
        ),
        users=UserSummary(
            total=len(user_roles),
            by_role=dict(role_counts),
        ),
        api_keys=ApiKeySummary(
            total=len(api_key_labels),
            labels=[str(label) for label in api_key_labels if label is not None],
        ),
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/orgs/{org_id}/overview",
    response_model=OrgTenantOverviewResponse,
)
def get_org_overview(
    org_id: str,
    db: Session = Depends(get_session),
) -> OrgTenantOverviewResponse:
    return _get_org_overview(org_id, db)


def _make_id(column, prefix: str, index: int):
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        return f"{prefix}-{index}"

    if python_type is int:
        return index
    if issubclass(python_type, UUID):
        return uuid4()
    return f"{prefix}-{index}"


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)

    now = datetime.now(timezone.utc)
    org_id = _make_id(Org.id, "org", 1)

    db = TestingSessionLocal()
    try:
        db.add(
            Org(
                id=org_id,
                name="Contract Test Organization",
                created_at=now,
            )
        )
        for index, (email, role) in enumerate(
            (
                ("admin@example.test", "admin"),
                ("member1@example.test", "member"),
                ("member2@example.test", "member"),
            ),
            start=1,
        ):
            db.add(
                User(
                    id=_make_id(User.id, "user", index),
                    email=email,
                    org_id=org_id,
                    role=role,
                    created_at=now,
                    password_hash="not-a-real-password-hash",
                )
            )
        for index, label in enumerate(("production", "ci"), start=1):
            db.add(
                ApiKey(
                    id=_make_id(ApiKey.id, "key", index),
                    org_id=org_id,
                    key_hash=f"contract-key-hash-{index}",
                    label=label,
                    created_at=now,
                )
            )
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(f"/api/orgs/{org_id}/overview")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["users"]["total"] == 3, body
    assert body["users"]["by_role"] == {"admin": 1, "member": 2}, body
    assert body["api_keys"]["total"] == 2, body
    assert body["api_keys"]["labels"] == ["production", "ci"], body
    assert "key_hash" not in response.text, response.text

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()
    raise SystemExit(0)