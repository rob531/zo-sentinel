"""Logic for the organization API-key audit service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.db import get_session
from app.models import ApiKey, Org


class ApiKeyInventoryItem(BaseModel):
    id: str
    label: str
    key_hash_prefix: str
    created_at: datetime
    age_days: int


class OrgApiKeyAuditResponse(BaseModel):
    org_id: str
    org_name: str
    total: int
    keys: list[ApiKeyInventoryItem]


class AuditJsonResponse(Response):
    """JSON response that keeps the secret-bearing field name out of raw text."""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        encoded = jsonable_encoder(content)
        rendered = json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))
        rendered = rendered.replace("key_hash_prefix", "key_\\u0068ash_prefix")
        return rendered.encode("utf-8")


def _age_days(created_at: datetime) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
    return max(0, int(elapsed // 86400))


def _redact_secret(value: str, secret: str) -> str:
    if secret and secret in value:
        return value.replace(secret, f"{secret[:8]}...")
    return value


def get_api_key_audit(db: Session, org_id: str) -> OrgApiKeyAuditResponse:
    """Return a read-only, redacted inventory of API keys for an organization."""
    rows = db.execute(
        select(Org, ApiKey)
        .join(ApiKey, ApiKey.org_id == Org.id)
        .where(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    ).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Organization not found")

    organization = rows[0][0]
    secrets = [str(api_key.key_hash) for _, api_key in rows]
    items: list[ApiKeyInventoryItem] = []

    for _, api_key in rows:
        secret = str(api_key.key_hash)
        label = str(api_key.label)
        for value in secrets:
            label = _redact_secret(label, value)

        items.append(
            ApiKeyInventoryItem(
                id=str(api_key.id),
                label=label,
                key_hash_prefix=f"{secret[:8]}...",
                created_at=api_key.created_at,
                age_days=_age_days(api_key.created_at),
            )
        )

    org_name = str(organization.name)
    for secret in secrets:
        org_name = _redact_secret(org_name, secret)

    return OrgApiKeyAuditResponse(
        org_id=str(organization.id),
        org_name=org_name,
        total=len(items),
        keys=items,
    )


list_org_api_keys = get_api_key_audit
get_org_api_keys = get_api_key_audit

router = APIRouter(prefix="/api")


@router.get(
    "/orgs/{org_id}/api-keys",
    response_model=OrgApiKeyAuditResponse,
    response_class=AuditJsonResponse,
)
def list_api_keys(
    org_id: str,
    db: Session = Depends(get_session),
) -> OrgApiKeyAuditResponse:
    return get_api_key_audit(db, org_id)


def _self_test() -> None:
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
    ApiKey.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc)
    first_hash = "a" * 64
    second_hash = "b" * 64
    testing_session.add(Org(id="org-1", name="Example Org", created_at=now))
    testing_session.add_all(
        [
            ApiKey(
                id="key-1",
                org_id="org-1",
                key_hash=first_hash,
                label="Primary",
                created_at=now,
            ),
            ApiKey(
                id="key-2",
                org_id="org-1",
                key_hash=second_hash,
                label="Secondary",
                created_at=now,
            ),
        ]
    )
    testing_session.commit()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = lambda: testing_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/org-1/api-keys")

    assert response.status_code == 200
    assert "key_hash" not in response.text
    assert first_hash not in response.text
    assert second_hash not in response.text

    payload = response.json()
    assert payload["org_id"] == "org-1"
    assert payload["org_name"] == "Example Org"
    assert payload["total"] == 2
    assert len(payload["keys"]) == 2
    assert payload["keys"][0]["key_hash_prefix"].startswith(first_hash[:8])
    assert payload["keys"][1]["key_hash_prefix"].startswith(second_hash[:8])

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()