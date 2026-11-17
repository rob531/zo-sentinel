"""Logic for the freshness SLA watchdog API."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat, McpLlmAxisScore, McpServerRegistry


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_dict(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _positive_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _surface_threshold(payload: Any, compute_seconds: Any) -> float:
    values = _payload_dict(payload)
    max_age = _positive_number(values.get("max_age_seconds"))
    if max_age is not None:
        return max_age
    oldest_age = _positive_number(values.get("oldest_age_seconds"))
    if oldest_age is not None:
        return oldest_age
    computed = _positive_number(compute_seconds)
    return computed if computed is not None else 0.0


def _surface_status(
    computed_at: Optional[datetime],
    threshold: float,
    now: datetime,
) -> tuple[str, float]:
    if computed_at is None:
        return "MISSING", 0.0
    age = max(0.0, (now - _as_utc(computed_at)).total_seconds())
    return ("STALE" if age > threshold else "OK"), age


def _registry_rows_without_axis_scores(db: Session) -> int:
    scored = (
        select(McpLlmAxisScore.id)
        .where(McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .exists()
    )
    servers_without_scores = select(McpServerRegistry.server_id).where(~scored)
    return int(
        db.execute(
            select(func.count()).select_from(servers_without_scores.subquery())
        ).scalar()
        or 0
    )


def get_registry_coverage(db: Session) -> Dict[str, int]:
    """Return registry assessment coverage without writing to the database."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.last_assessed,
        )
    ).all()

    never_assessed = 0
    never_assessed_or_stale = 0
    for _server_id, last_assessed in rows:
        if last_assessed is None:
            never_assessed += 1
            never_assessed_or_stale += 1
            continue
        assessed = _as_utc(last_assessed)
        if assessed < cutoff:
            never_assessed_or_stale += 1

    return {
        "never_assessed_servers": never_assessed,
        "never_assessed_or_stale_servers": never_assessed_or_stale,
        "servers_without_axis_scores": _registry_rows_without_axis_scores(db),
        "total_servers": len(rows),
    }


def get_freshness_sla(db: Session) -> Dict[str, Any]:
    """Compute freshness statuses and registry coverage for the SLA API."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.payload,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
        )
    ).all()

    surfaces: list[Dict[str, Any]] = []
    stale_surfaces = 0
    missing_surfaces = 0

    for surface, payload, computed_at, compute_seconds in rows:
        threshold = _surface_threshold(payload, compute_seconds)
        status, age_seconds = _surface_status(computed_at, threshold, now)
        if status == "STALE":
            stale_surfaces += 1
        elif status == "MISSING":
            missing_surfaces += 1

        surfaces.append(
            {
                "surface": surface,
                "computed_at": computed_at.isoformat() if computed_at else None,
                "age_seconds": age_seconds,
                "compute_seconds": compute_seconds,
                "status": status,
            }
        )

    coverage = get_registry_coverage(db)
    return {
        "surfaces": surfaces,
        "summary": {
            "stale_surfaces": stale_surfaces,
            "missing_surfaces": missing_surfaces,
            "never_assessed_servers": coverage["never_assessed_servers"],
            "total_servers": coverage["total_servers"],
        },
        "generated_at": now.isoformat(),
    }


def calculate_freshness_sla(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the calculation-style name."""
    return get_freshness_sla(db)


def get_freshness_sla_report(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the report-style name."""
    return get_freshness_sla(db)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    db = SessionLocal()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            FreshnessStat(
                surface="stale-surface",
                payload={"max_age_seconds": 60},
                computed_at=now - timedelta(hours=2),
                compute_seconds=10,
            ),
            FreshnessStat(
                surface="fresh-surface",
                payload={"max_age_seconds": 3600},
                computed_at=now,
                compute_seconds=10,
            ),
            FreshnessStat(
                surface="empty-payload-surface",
                payload={},
                computed_at=now,
                compute_seconds=3600,
            ),
        ]
    )
    db.add_all(
        [
            McpServerRegistry(server_id="server-1", last_assessed=now),
            McpServerRegistry(server_id="server-2", last_assessed=None),
            McpServerRegistry(
                server_id="server-3",
                last_assessed=now - timedelta(days=10),
            ),
            McpServerRegistry(server_id="server-4", last_assessed=now),
        ]
    )
    db.add(
        McpLlmAxisScore(
            id="score-1",
            server_id="server-1",
            axis_name="safety",
            model_version="v1",
        )
    )
    db.commit()

    that_app = FastAPI()

    def override_get_session():
        yield db

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/freshness/sla")
    def freshness_sla(session: Session = Depends(get_session)):
        return get_freshness_sla(session)

    from fastapi.testclient import TestClient

    response = TestClient(that_app).get("/api/freshness/sla")
    assert response.status_code == 200
    payload = response.json()
    assert sum(item["status"] == "STALE" for item in payload["surfaces"]) == 1
    assert payload["summary"]["never_assessed_servers"] == 1
    print("PASS")
    db.close()