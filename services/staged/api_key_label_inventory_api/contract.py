from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org


class OrgLabelInventory(BaseModel):
    org_id: str
    org_name: str
    keys: int
    distinct_labels: int
    unlabelled_keys: int
    duplicate_labels: int
    created_last_30d: int


class LabelInventoryTotals(BaseModel):
    orgs: int
    keys: int


class LabelInventoryResponse(BaseModel):
    orgs: list[OrgLabelInventory]
    totals: LabelInventoryTotals


application = FastAPI(title="API Key Label Inventory API")


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _label_inventory(
    session: Session,
    now: datetime | None = None,
) -> LabelInventoryResponse:
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=30)

    statement = (
        select(
            ApiKey.id,
            ApiKey.label,
            ApiKey.created_at,
            ApiKey.org_id,
            Org.name,
        )
        .join(Org, ApiKey.org_id == Org.id)
    )
    rows = session.execute(statement).all()

    grouped: dict[str, dict[str, Any]] = {}
    total_keys = 0

    for key_id, label, created_at, org_id, org_name in rows:
        org_key = str(org_id)
        total_keys += 1

        group = grouped.setdefault(
            org_key,
            {
                "org_name": str(org_name) if org_name is not None else org_key,
                "keys": 0,
                "unlabelled_keys": 0,
                "created_last_30d": 0,
                "labels": Counter(),
            },
        )
        group["keys"] += 1

        label_value = "" if label is None else str(label)
        if not label_value.strip():
            group["unlabelled_keys"] += 1
        else:
            group["labels"][label_value] += 1

        created = _coerce_datetime(created_at)
        if created is not None and created >= cutoff:
            group["created_last_30d"] += 1

    orgs = []
    for org_id in sorted(grouped):
        group = grouped[org_id]
        label_counts = group["labels"]
        orgs.append(
            OrgLabelInventory(
                org_id=org_id,
                org_name=group["org_name"],
                keys=group["keys"],
                distinct_labels=len(label_counts),
                unlabelled_keys=group["unlabelled_keys"],
                duplicate_labels=sum(
                    count > 1 for count in label_counts.values()
                ),
                created_last_30d=group["created_last_30d"],
            )
        )

    return LabelInventoryResponse(
        orgs=orgs,
        totals=LabelInventoryTotals(
            orgs=len(orgs),
            keys=total_keys,
        ),
    )


@application.get(
    "/api/api-keys/label-inventory",
    response_model=LabelInventoryResponse,
)
def get_label_inventory(
    session: Session = Depends(get_session),
) -> LabelInventoryResponse:
    return _label_inventory(session)


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(
            _contains_key(child, key) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def run() -> None:
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
    ApiKey.__table__.create(engine)

    now = datetime.now(timezone.utc)
    session = TestingSessionLocal()
    try:
        session.add_all(
            [
                Org(id="o1", name="Acme", created_at=now),
                Org(id="o2", name="Example", created_at=now),
                ApiKey(
                    id="k1",
                    org_id="o1",
                    key_hash="hash-one",
                    label="shared-label",
                    created_at=now,
                ),
                ApiKey(
                    id="k2",
                    org_id="o1",
                    key_hash="hash-two",
                    label="shared-label",
                    created_at=now - timedelta(days=1),
                ),
                ApiKey(
                    id="k3",
                    org_id="o2",
                    key_hash="hash-three",
                    label=None,
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        test_session = TestingSessionLocal()
        try:
            yield test_session
        finally:
            test_session.close()

    application.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(application) as client:
            response = client.get("/api/api-keys/label-inventory")

        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["keys"] == 3
        assert not _contains_key(payload, "key_hash")

        acme = next(org for org in payload["orgs"] if org["org_id"] == "o1")
        assert acme["duplicate_labels"] == 1
    finally:
        application.dependency_overrides.pop(get_session, None)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()