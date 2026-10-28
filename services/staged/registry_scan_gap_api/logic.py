from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc_naive(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def read_scan_gaps(
    days: int = 90,
    limit: int = 200,
    db: Optional[Session] = None,
) -> dict:
    """Return registered servers that have never been scanned or are stale."""
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise ValueError("days must be a non-negative integer")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")

    session_iterator = None
    if db is None:
        session_iterator = get_session()
        db = next(session_iterator)

    try:
        now = _utc_naive_now()
        cutoff = now - timedelta(days=days)
        statement = select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
        ).where(
            or_(
                McpServerRegistry.last_scanned.is_(None),
                McpServerRegistry.last_scanned <= cutoff,
            )
        )

        rows = db.execute(statement).mappings().all()
        gaps = []
        for row in rows:
            last_scanned = _as_utc_naive(row["last_scanned"])
            if last_scanned is None:
                state = "never_scanned"
                age_days = 0
            else:
                age_days = max(0, (now - last_scanned).days)
                state = "stale"

            gaps.append(
                {
                    "server_id": row["server_id"],
                    "name": row["name"],
                    "registry_source": row["registry_source"],
                    "risk_tier": row["risk_tier"],
                    "scan_count": row["scan_count"],
                    "last_scanned": row["last_scanned"],
                    "age_days": int(age_days),
                    "state": state,
                }
            )

        gaps.sort(key=lambda item: (item["state"] != "never_scanned", -item["age_days"], str(item["server_id"])))
        return {
            "days": days,
            "count": len(gaps),
            "gaps": gaps[:limit],
        }
    finally:
        if session_iterator is not None:
            try:
                session_iterator.close()
            except AttributeError:
                pass


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    registry_source="test",
                    risk_tier="medium",
                    scan_count=0,
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=None,
                    last_assessed=None,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale server",
                    registry_source="test",
                    risk_tier="high",
                    scan_count=1,
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=now - timedelta(days=10),
                    last_assessed=now - timedelta(days=10),
                ),
                McpServerRegistry(
                    server_id="current-server",
                    name="Current server",
                    registry_source="test",
                    risk_tier="low",
                    scan_count=2,
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=now,
                    last_assessed=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/registry/scan-gaps")
    def scan_gaps(
        days: int = 90,
        limit: int = 200,
        session: Session = Depends(get_session),
    ):
        return read_scan_gaps(days=days, limit=limit, db=session)

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/scan-gaps", params={"days": 10})
            assert response.status_code == 200
            payload = response.json()
            assert payload["count"] == 2
            never_scanned = next(
                item for item in payload["gaps"] if item["server_id"] == "never-scanned"
            )
            assert never_scanned["state"] == "never_scanned"
            assert isinstance(never_scanned["age_days"], int)
            assert all(isinstance(item["age_days"], int) for item in payload["gaps"])
    finally:
        McpServerRegistry.__table__.drop(engine)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()