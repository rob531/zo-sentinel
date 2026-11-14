"""HTTP surface for the attestation expiry ledger."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_expiry_ledger

router = APIRouter(prefix="/api", tags=["attestation-expiry-ledger"])

ExpiryBucket = Literal["expired_7d", "expiring_30d", "expired", "valid"]


class BucketCount(BaseModel):
    bucket: ExpiryBucket
    count: int


class ServerExpiry(BaseModel):
    server_id: str
    name: str | None = None
    expires_at: datetime
    days_remaining: int
    bucket: ExpiryBucket


class ExpiryLedgerResponse(BaseModel):
    as_of: datetime
    buckets: list[BucketCount]
    servers: list[ServerExpiry]


@router.get("/attestations/expiry-ledger", response_model=ExpiryLedgerResponse)
def attestation_expiry_ledger(
    db: Session = Depends(get_session),
) -> ExpiryLedgerResponse:
    result = get_expiry_ledger(db)
    if isinstance(result, dict):
        return ExpiryLedgerResponse.model_validate(result)
    return ExpiryLedgerResponse.model_validate(result, from_attributes=True)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    metadata = MetaData()
    attestations = Table(
        "mcp_attestations",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", String, nullable=False),
        Column("expires_at", DateTime, nullable=False),
    )
    attestations.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        ("server-expired", "Expired server", now - timedelta(days=10)),
        ("server-10d", "Ten-day server", now + timedelta(days=10)),
        ("server-90d", "Ninety-day server", now + timedelta(days=90)),
        ("server-valid", "Valid server", now + timedelta(days=365)),
    ]

    with Session(engine) as db:
        for server_id, name, _ in rows:
            db.add(McpServerRegistry(server_id=server_id, name=name))
        db.flush()
        for server_id, _, expires_at in rows:
            db.execute(
                insert(attestations).values(
                    server_id=server_id,
                    expires_at=expires_at,
                )
            )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/attestations/expiry-ledger")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert sum(item["count"] for item in payload["buckets"]) == 4
    expiring_30d = next(
        item for item in payload["buckets"] if item["bucket"] == "expiring_30d"
    )
    assert expiring_30d["count"] == 1
    ten_day_server = next(
        item for item in payload["servers"] if item["server_id"] == "server-10d"
    )
    assert ten_day_server["bucket"] == "expiring_30d"
    print("PASS")


if __name__ == "__main__":
    _self_test()