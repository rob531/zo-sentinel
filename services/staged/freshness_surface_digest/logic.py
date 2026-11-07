"""logic.py -- the service's data/computation layer (one concern, one file).

Mirrors the exemplar doctrine: import the REAL data layer (app.db/app.models),
never an inline/stub/SQLite-dict model. Pure functions over a Session so the
router stays thin and the contract can exercise this directly.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


_MAX_AGE = timedelta(hours=24)


def _computed_at_text(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload_summary(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            decoded = payload
    else:
        decoded = payload

    if isinstance(decoded, dict):
        payload_keys = [str(key) for key in decoded.keys()]
    else:
        payload_keys = []

    try:
        serialized = json.dumps(
            decoded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        serialized = json.dumps(str(decoded), ensure_ascii=False)

    return {
        "payload_keys": payload_keys,
        "payload_size": len(serialized.encode("utf-8")),
    }


def get_freshness_surface_digest(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Return a compact digest of the latest freshness stat for each surface."""
    rows = db.execute(
        select(FreshnessStat)
        .order_by(FreshnessStat.surface.asc(), FreshnessStat.computed_at.desc())
    ).scalars().all()

    now = datetime.now(timezone.utc)
    seen = set()
    surfaces: List[Dict[str, Any]] = []

    for row in rows:
        if row.surface in seen:
            continue
        seen.add(row.surface)

        computed_at = _computed_at_text(row.computed_at)
        try:
            stale = now - _as_datetime(row.computed_at) > _MAX_AGE
        except (TypeError, ValueError, OverflowError):
            stale = False

        summary = _payload_summary(row.payload)
        surfaces.append(
            {
                "surface": str(row.surface),
                "computed_at": computed_at,
                "compute_seconds": float(row.compute_seconds),
                "payload_keys": summary["payload_keys"],
                "payload_size": summary["payload_size"],
                "stale": bool(stale),
            }
        )

    return {"surfaces": surfaces}


# Keep the common service-level names available to thin routers and contracts.
get_freshness_surfaces = get_freshness_surface_digest
get_surface_digest = get_freshness_surface_digest
list_surfaces = get_freshness_surface_digest
surface_digest = get_freshness_surface_digest


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
    FreshnessStat.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    session = TestingSession()
    now = datetime.now(timezone.utc)
    session.add(
        FreshnessStat(
            surface="fresh",
            computed_at=now,
            compute_seconds=1.25,
            payload={"fresh": True, "count": 2},
        )
    )
    session.add(
        FreshnessStat(
            surface="stale",
            computed_at=now - timedelta(hours=25),
            compute_seconds=2.5,
            payload={"stale": True},
        )
    )
    session.commit()

    that_app = FastAPI()

    @that_app.get("/api/freshness/surfaces")
    def freshness_surfaces(db: Session = Depends(get_session)):
        return get_freshness_surface_digest(db)

    that_app.dependency_overrides[get_session] = lambda: session

    try:
        response = TestClient(that_app).get("/api/freshness/surfaces")
        body = response.json()
        assert response.status_code == 200
        assert len(body["surfaces"]) == 2
        by_surface = {item["surface"]: item for item in body["surfaces"]}
        assert by_surface["fresh"]["stale"] is False
        assert by_surface["stale"]["stale"] is True
        assert by_surface["fresh"]["payload_keys"] == ["fresh", "count"]
        print("PASS")
    finally:
        session.close()
        engine.dispose()