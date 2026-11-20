"""Data and computation layer for registry verdict staleness bands."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

BANDS = (
    "0-1d",
    "1-7d",
    "7-30d",
    "30-90d",
    "over-90d",
    "never-assessed",
)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    else:
        return datetime.min.replace(tzinfo=timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _band_for_age(age_days: Optional[float]) -> str:
    if age_days is None:
        return "never-assessed"
    if age_days <= 1:
        return "0-1d"
    if age_days <= 7:
        return "1-7d"
    if age_days <= 30:
        return "7-30d"
    if age_days <= 90:
        return "30-90d"
    return "over-90d"


def get_verdict_staleness_bands(
    db: Session,
    days: int = 30,
    *,
    horizon_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Build verdict-staleness bands and the oldest stale registry entries."""
    if horizon_days is not None:
        days = horizon_days
    days = int(days)
    if days < 0:
        raise ValueError("days must be non-negative")

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
    ).all()

    now = datetime.now(timezone.utc)
    counts = {band: 0 for band in BANDS}
    stale_entries: List[Tuple[Optional[datetime], str, Dict[str, Any]]] = []

    for server_id, name, risk_tier, last_assessed in rows:
        assessed_at = _parse_timestamp(last_assessed)
        age_days = None
        if assessed_at is not None:
            age_days = max(0.0, (now - assessed_at).total_seconds() / 86400.0)

        band = _band_for_age(age_days)
        counts[band] += 1

        if assessed_at is None or age_days is None or age_days > days:
            entry = {
                "server_id": str(server_id),
                "name": name if name is None else str(name),
                "risk_tier": risk_tier if risk_tier is None else str(risk_tier),
                "last_assessed": _serialize_timestamp(last_assessed),
            }
            stale_entries.append((assessed_at, str(server_id), entry))

    total = len(rows)
    stale_entries.sort(key=lambda item: (item[0] is not None, item[0] or datetime.min.replace(tzinfo=timezone.utc), item[1]))

    return {
        "horizon_days": days,
        "total": total,
        "bands": [
            {
                "band": band,
                "count": counts[band],
                "share": round(counts[band] / total, 4) if total else 0.0,
            }
            for band in BANDS
        ],
        "top_stale": [entry for _, _, entry in stale_entries[:20]],
    }


def get_verdict_staleness(
    db: Session,
    days: int = 30,
    *,
    horizon_days: Optional[int] = None,
) -> Dict[str, Any]:
    return get_verdict_staleness_bands(
        db, days, horizon_days=horizon_days
    )


def compute_verdict_staleness(
    db: Session,
    days: int = 30,
    *,
    horizon_days: Optional[int] = None,
) -> Dict[str, Any]:
    return get_verdict_staleness_bands(
        db, days, horizon_days=horizon_days
    )


def get_staleness_bands(
    db: Session,
    days: int = 30,
    *,
    horizon_days: Optional[int] = None,
) -> Dict[str, Any]:
    return get_verdict_staleness_bands(
        db, days, horizon_days=horizon_days
    )


if __name__ == "__main__":
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    McpServerRegistry.__table__.create(engine)
    TestSession = sessionmaker(bind=engine)

    try:
        with TestSession() as session:
            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    McpServerRegistry(
                        server_id="never",
                        name="Never assessed",
                        last_assessed=None,
                    ),
                    McpServerRegistry(
                        server_id="recent",
                        name="Recent",
                        last_assessed=now - timedelta(hours=1),
                    ),
                    McpServerRegistry(
                        server_id="old",
                        name="Old",
                        last_assessed=now - timedelta(days=45),
                    ),
                    McpServerRegistry(
                        server_id="oldest",
                        name="Oldest",
                        last_assessed=now - timedelta(days=120),
                    ),
                ]
            )
            session.commit()

            result = get_verdict_staleness_bands(session, days=30)
            assert result["total"] == 4
            assert sum(item["count"] for item in result["bands"]) == 4
            assert any(
                "never" in item["band"].lower() and item["count"] == 1
                for item in result["bands"]
            )
            assert len(result["top_stale"]) == 3
        print("PASS")
    finally:
        engine.dispose()