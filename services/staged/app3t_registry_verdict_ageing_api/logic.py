"""logic.py -- the service's data/computation layer (one concern, one file).

Reads the authoritative application registry through app.db/app.models. No
network access, writes, inline models, or alternative data stores are used.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")

_BANDS = ("0-1", "2-7", "8-30", "31-90", "91+")
_BAND_BOUNDS = ((0, 1), (2, 7), (8, 30), (31, 90), (91, None))


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _age_days(value: Any, now: datetime) -> Optional[int]:
    assessed = _as_utc(value)
    if assessed is None:
        return None
    age = (now - assessed).days
    return max(0, age)


def _band_for_age(age_days: int) -> str:
    for (lower, upper), band in zip(_BAND_BOUNDS, _BANDS):
        if age_days >= lower and (upper is None or age_days <= upper):
            return band
    return "91+"


def compute_verdict_ageing(
    db: Session,
    days: int = 30,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute registry verdict-ageing statistics for the supplied filter.

    ``days`` is the age threshold used for the stale-server list. Servers with
    a NULL ``last_assessed`` value are included in ``totals.servers`` and
    ``totals.never_assessed`` only; they do not receive an ageing band.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.verdict,
        McpServerRegistry.last_assessed,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(statement).all()
    counts = {band: 0 for band in _BANDS}
    stale: List[Dict[str, Any]] = []
    never_assessed = 0
    assessed_total = 0

    for row in rows:
        age_days = _age_days(row.last_assessed, now)
        if age_days is None:
            never_assessed += 1
            continue

        assessed_total += 1
        counts[_band_for_age(age_days)] += 1
        if age_days > days:
            stale.append(
                {
                    "server_id": row.server_id,
                    "name": row.name,
                    "verdict": row.verdict,
                    "last_assessed": row.last_assessed,
                    "age_days": age_days,
                }
            )

    stale.sort(key=lambda item: (-item["age_days"], str(item["server_id"])))
    stale = stale[:100]

    buckets = [
        {
            "band": band,
            "count": counts[band],
            "pct": round(counts[band] * 100.0 / assessed_total, 2)
            if assessed_total
            else 0.0,
        }
        for band in _BANDS
    ]

    return {
        "days": days,
        "registry_source": registry_source,
        "buckets": buckets,
        "totals": {
            "servers": len(rows),
            "never_assessed": never_assessed,
        },
        "stale_servers": stale,
    }


# A descriptive alias keeps the computation convenient for callers that use
# the endpoint's resource name.
get_verdict_ageing = compute_verdict_ageing


@router.get("/registry/verdict-ageing")
def registry_verdict_ageing(
    days: int = Query(30, ge=0),
    registry_source: Optional[str] = Query(None),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return compute_verdict_ageing(db, days=days, registry_source=registry_source)


if __name__ == "__main__":
    from fastapi import FastAPI
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
    test_session = TestSession()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="age-0",
                name="age 0",
                registry_source="test",
                verdict="trusted",
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="age-3",
                name="age 3",
                registry_source="test",
                verdict="trusted",
                last_assessed=now - __import__("datetime").timedelta(days=3),
            ),
            McpServerRegistry(
                server_id="age-40",
                name="age 40",
                registry_source="test",
                verdict="trusted",
                last_assessed=now - __import__("datetime").timedelta(days=40),
            ),
            McpServerRegistry(
                server_id="age-200",
                name="age 200",
                registry_source="test",
                verdict="trusted",
                last_assessed=now - __import__("datetime").timedelta(days=200),
            ),
            McpServerRegistry(
                server_id="never",
                name="never assessed",
                registry_source="test",
                verdict="unknown",
                last_assessed=None,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_session
    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/verdict-ageing",
            params={"days": 30, "registry_source": "test"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["servers"] == 5
        assert payload["totals"]["never_assessed"] == 1
        assert sum(bucket["count"] for bucket in payload["buckets"]) == 4
        assert any(
            item["server_id"] == "age-200" for item in payload["stale_servers"]
        )

    test_session.close()
    test_engine.dispose()
    print("PASS")