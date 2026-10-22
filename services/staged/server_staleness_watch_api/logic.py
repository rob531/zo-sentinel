from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Optional[datetime], now: datetime) -> Optional[int]:
    value = _as_utc(value)
    if value is None:
        return None
    return max(0, (now - value).days)


def get_staleness_data(
    db: Session = Depends(get_session),
    days: int = 7,
    risk_tier: Optional[str] = None,
) -> dict:
    days = int(days)
    if days < 0:
        raise ValueError("days must be non-negative")

    query = db.query(McpServerRegistry)
    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    rows = query.order_by(McpServerRegistry.server_id).all()
    now = datetime.now(timezone.utc)
    threshold = timedelta(days=days)

    stale = []
    by_registry_source = {}
    by_risk_tier = {}

    for row in rows:
        last_seen = _as_utc(row.last_seen)
        last_scanned = _as_utc(row.last_scanned)
        days_since_seen = _age_days(row.last_seen, now)
        days_since_scan = _age_days(row.last_scanned, now)

        is_stale = last_scanned is None or now - last_scanned > threshold
        if not is_stale:
            continue

        stale.append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "registry_source": row.registry_source,
                "risk_tier": row.risk_tier,
                "last_seen": row.last_seen,
                "last_scanned": row.last_scanned,
                "scan_count": row.scan_count,
                "days_since_scan": days_since_scan,
            }
        )

        if row.registry_source is not None:
            by_registry_source[row.registry_source] = (
                by_registry_source.get(row.registry_source, 0) + 1
            )
        if row.risk_tier is not None:
            by_risk_tier[row.risk_tier] = by_risk_tier.get(row.risk_tier, 0) + 1

        _ = last_seen, days_since_seen

    return {
        "days": days,
        "stale": stale,
        "count": len(stale),
        "by_registry_source": by_registry_source,
        "by_risk_tier": by_risk_tier,
    }


get_staleness = get_staleness_data
get_server_staleness = get_staleness_data
get_stale_servers = get_staleness_data
calculate_staleness = get_staleness_data


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    now = datetime.now(timezone.utc)
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="recent",
                name="Recently scanned",
                registry_source="registry-a",
                risk_tier="low",
                trust_score=0.9,
                last_seen=now - timedelta(days=1),
                last_scanned=now - timedelta(days=1),
                scan_count=10,
                first_seen=now - timedelta(days=30),
            ),
            McpServerRegistry(
                server_id="old",
                name="Oldly scanned",
                registry_source="registry-b",
                risk_tier="high",
                trust_score=0.4,
                last_seen=now - timedelta(days=12),
                last_scanned=now - timedelta(days=12),
                scan_count=2,
                first_seen=now - timedelta(days=60),
            ),
            McpServerRegistry(
                server_id="never-scanned",
                name="Never scanned",
                registry_source="registry-c",
                risk_tier="medium",
                trust_score=0.5,
                last_seen=now - timedelta(days=4),
                last_scanned=None,
                scan_count=0,
                first_seen=now - timedelta(days=4),
            ),
            McpServerRegistry(
                server_id="recent-two",
                name="Also recently scanned",
                registry_source="registry-a",
                risk_tier="low",
                trust_score=0.8,
                last_seen=now - timedelta(hours=6),
                last_scanned=now - timedelta(hours=6),
                scan_count=8,
                first_seen=now - timedelta(days=20),
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()

    def override_get_session():
        yield test_db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/servers/staleness")
    def staleness_endpoint(
        days: int = 7,
        risk_tier: Optional[str] = None,
        db: Session = Depends(get_session),
    ):
        return get_staleness_data(db=db, days=days, risk_tier=risk_tier)

    client = TestClient(test_app)
    response = client.get("/api/servers/staleness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["days"] == 7
    assert "high" in payload["by_risk_tier"]
    assert "recent" not in {item["server_id"] for item in payload["stale"]}

    filtered = client.get("/api/servers/staleness", params={"risk_tier": "high"})
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 1
    assert filtered.json()["stale"][0]["server_id"] == "old"

    test_db.close()
    test_engine.dispose()
    print("PASS")