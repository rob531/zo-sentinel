"""Decision expiry computation over the real application data layer."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from fastapi import Depends, FastAPI
from sqlalchemy import Column, DateTime, JSON, MetaData, String, Table, Text, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


_decisions_metadata = MetaData()

mcp_decisions = Table(
    "mcp_decisions",
    _decisions_metadata,
    Column("server_id", String, primary_key=True),
    Column("decision", Text, nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("conditions", JSON, nullable=True),
)


class ExpiryData(tuple):
    """Combined expiry results, usable as a pair or by named key."""

    def __new__(cls, expiring: list[dict[str, Any]], expired: list[dict[str, Any]]):
        return super().__new__(cls, (expiring, expired))

    @property
    def expiring(self) -> list[dict[str, Any]]:
        return self[0]

    @property
    def expired(self) -> list[dict[str, Any]]:
        return self[1]

    @property
    def expiring_count(self) -> int:
        return len(self[0])

    @property
    def expired_count(self) -> int:
        return len(self[1])

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            values = {
                "expiring": self.expiring,
                "expired": self.expired,
                "expiring_count": self.expiring_count,
                "expired_count": self.expired_count,
            }
            return values[key]
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_remaining(expires_at: datetime, now: datetime) -> int:
    return math.ceil((expires_at - now).total_seconds() / 86400.0)


def get_expiry_data(db: Session, days: int = 7) -> ExpiryData:
    """Return currently valid decisions expiring within days and expired decisions."""
    if days < 0:
        raise ValueError("days must be non-negative")

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)

    rows = db.execute(
        select(
            mcp_decisions.c.server_id,
            mcp_decisions.c.decision,
            mcp_decisions.c.expires_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).join(
            McpServerRegistry,
            McpServerRegistry.server_id == mcp_decisions.c.server_id,
        )
    ).all()

    expiring: list[tuple[datetime, dict[str, Any]]] = []
    expired: list[tuple[datetime, dict[str, Any]]] = []

    for server_id, decision, expires_at, name, risk_tier in rows:
        expires_at_utc = _as_utc(expires_at)
        entry = {
            "server_id": server_id,
            "name": name,
            "decision": decision,
            "risk_tier": risk_tier,
            "expires_at": expires_at_utc.isoformat(),
            "days_remaining": _days_remaining(expires_at_utc, now),
        }

        if expires_at_utc < now:
            expired.append((expires_at_utc, entry))
        elif expires_at_utc <= horizon:
            expiring.append((expires_at_utc, entry))

    expiring_entries = [entry for _, entry in sorted(expiring, key=lambda row: row[0])]
    expired_entries = [entry for _, entry in sorted(expired, key=lambda row: row[0])]
    return ExpiryData(expiring_entries, expired_entries)


def get_expiry_summary(db: Session, days: int = 7) -> dict[str, Any]:
    """Return expiry lists and their counts."""
    result = get_expiry_data(db, days)
    return {
        "expiring_count": result.expiring_count,
        "expired_count": result.expired_count,
        "expiring": result.expiring,
        "expired": result.expired,
    }


def get_decision_expiry(db: Session, days: int = 7) -> ExpiryData:
    return get_expiry_data(db, days)


def get_expiry_watch(db: Session, days: int = 7) -> ExpiryData:
    return get_expiry_data(db, days)


def check_expiry(db: Session, days: int = 7) -> ExpiryData:
    return get_expiry_data(db, days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.metadata.create_all(engine)
    mcp_decisions.create(engine)

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with session_factory() as session:
        for index, risk_tier in enumerate(("low", "medium", "high"), start=1):
            session.add(
                McpServerRegistry(
                    server_id=f"server-{index}",
                    name=f"Server {index}",
                    risk_tier=risk_tier,
                )
            )
        session.flush()

        session.execute(
            mcp_decisions.insert(),
            [
                {
                    "server_id": "server-1",
                    "decision": "allow",
                    "decided_at": now - timedelta(days=1),
                    "expires_at": now + timedelta(days=2),
                    "conditions": None,
                },
                {
                    "server_id": "server-2",
                    "decision": "deny",
                    "decided_at": now - timedelta(days=10),
                    "expires_at": now - timedelta(days=1),
                    "conditions": None,
                },
                {
                    "server_id": "server-3",
                    "decision": "allow",
                    "decided_at": now,
                    "expires_at": now + timedelta(days=40),
                    "conditions": None,
                },
            ],
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/decisions/expiry")
    def decisions_expiry(days: int = 7, db: Session = Depends(get_session)) -> dict[str, Any]:
        result = get_expiry_data(db, days)
        return {
            "days": days,
            "expiring_count": result.expiring_count,
            "expired_count": result.expired_count,
            "expiring": result.expiring,
            "expired": result.expired,
        }

    response = TestClient(that_app).get("/api/decisions/expiry", params={"days": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["expiring_count"] == 1
    assert payload["expired_count"] == 1
    assert payload["expired"][0]["days_remaining"] <= 0
    print("PASS")