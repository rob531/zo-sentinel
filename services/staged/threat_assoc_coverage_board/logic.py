"""Data and computation layer for threat-association coverage reporting."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


DEFAULT_STALE_AFTER_DAYS = 30
_TIMESTAMP_COLUMNS = ("created_at", "observed_at", "associated_at", "timestamp")


def _as_datetime(value: Any) -> Optional[datetime]:
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
            return None
    else:
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_timestamp(value: Any) -> Optional[str]:
    parsed = _as_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _reflect_association_table(db: Session) -> Optional[Table]:
    bind = db.get_bind()
    table = Table(
        "mcp_threat_associations",
        MetaData(),
        autoload_with=bind,
    )
    return table


def _coverage_report(db: Session, stale_after_days: int) -> Dict[str, Any]:
    stale_days = max(0, int(stale_after_days))
    total_servers = int(
        db.execute(select(func.count(McpServerRegistry.server_id))).scalar() or 0
    )

    registry_ids: Set[Any] = {
        row[0]
        for row in db.execute(select(McpServerRegistry.server_id)).all()
        if row[0] is not None
    }

    try:
        associations = _reflect_association_table(db)
    except Exception:
        associations = None

    source_rows: List[Dict[str, Any]] = []
    if associations is not None:
        available = set(associations.c.keys())
        if "source" in available and "server_id" in available:
            source_col = associations.c.source
            association_server_col = associations.c.server_id

            pulse_col = None
            if "pulse_id" in available:
                pulse_col = associations.c.pulse_id
            elif "pulse_name" in available:
                pulse_col = associations.c.pulse_name

            timestamp_col = next(
                (associations.c[name] for name in _TIMESTAMP_COLUMNS if name in available),
                None,
            )

            selected = [
                source_col,
                association_server_col,
                McpServerRegistry.server_id.label("registry_server_id"),
            ]
            if pulse_col is not None:
                selected.append(pulse_col.label("pulse"))
            if timestamp_col is not None:
                selected.append(timestamp_col.label("association_timestamp"))

            rows = db.execute(
                select(*selected).select_from(
                    associations.outerjoin(
                        McpServerRegistry,
                        McpServerRegistry.server_id == association_server_col,
                    )
                )
            ).all()

            for row in rows:
                values = row._mapping
                registry_server_id = values.get("registry_server_id")
                if registry_server_id is None:
                    continue
                source = values.get("source")
                source_rows.append(
                    {
                        "source": str(source) if source is not None else "unknown",
                        "server_id": values.get("server_id"),
                        "pulse": values.get("pulse"),
                        "timestamp": values.get("association_timestamp"),
                    }
                )

    servers_with_associations = len(
        {
            row["server_id"]
            for row in source_rows
            if row["server_id"] is not None
        }
    )

    coverage_pct = (
        round(servers_with_associations / total_servers * 100, 2)
        if total_servers
        else 0.0
    )
    coverage_pct = min(100.0, max(0.0, float(coverage_pct)))

    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in source_rows:
        grouped.setdefault(row["source"], []).append(row)

    by_source: List[Dict[str, Any]] = []
    for source in sorted(grouped):
        rows = grouped[source]
        servers = {
            row["server_id"]
            for row in rows
            if row["server_id"] is not None
        }
        pulses = {
            row["pulse"]
            for row in rows
            if row["pulse"] is not None
        }

        newest_by_server: Dict[Any, datetime] = {}
        oldest_seen: Optional[datetime] = None
        newest_seen: Optional[datetime] = None
        for row in rows:
            timestamp = _as_datetime(row["timestamp"])
            if timestamp is None:
                continue
            oldest_seen = (
                timestamp
                if oldest_seen is None or timestamp < oldest_seen
                else oldest_seen
            )
            newest_seen = (
                timestamp
                if newest_seen is None or timestamp > newest_seen
                else newest_seen
            )
            server_id = row["server_id"]
            if server_id is not None:
                previous = newest_by_server.get(server_id)
                if previous is None or timestamp > previous:
                    newest_by_server[server_id] = timestamp

        stale_servers = sum(
            1 for timestamp in newest_by_server.values() if timestamp < cutoff
        )
        by_source.append(
            {
                "source": source,
                "servers": int(len(servers)),
                "pulses": int(len(pulses)),
                "stale_servers": int(stale_servers),
                "oldest_seen": _iso_timestamp(oldest_seen),
                "newest_seen": _iso_timestamp(newest_seen),
            }
        )

    return {
        "total_servers_in_registry": total_servers,
        "servers_with_associations": int(servers_with_associations),
        "coverage_pct": coverage_pct,
        "stale_after_days": stale_days,
        "by_source": by_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_threat_coverage(
    db: Session,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> Dict[str, Any]:
    """Return registry coverage and freshness by threat-association source."""
    return _coverage_report(db, stale_after_days)


def get_coverage(
    db: Session,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> Dict[str, Any]:
    """Compatibility alias for the threat-coverage computation."""
    return get_threat_coverage(db, stale_after_days)


def compute_coverage(
    db: Session,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> Dict[str, Any]:
    """Compatibility alias for the threat-coverage computation."""
    return get_threat_coverage(db, stale_after_days)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)

    association_metadata = MetaData()
    association_table = Table(
        "mcp_threat_associations",
        association_metadata,
        Column("server_id", String, primary_key=True),
        Column("source", String, nullable=False),
        Column("pulse_id", String),
        Column("pulse_name", String),
        Column("confidence", Integer),
        Column("severity", String),
        Column("created_at", DateTime),
    )
    association_metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="srv-1", name="Server One"),
                McpServerRegistry(server_id="srv-2", name="Server Two"),
                McpServerRegistry(server_id="srv-3", name="Server Three"),
            ]
        )
        session.execute(
            association_table.insert(),
            [
                {
                    "server_id": "srv-1",
                    "source": "alpha",
                    "pulse_id": "pulse-1",
                    "pulse_name": "Pulse One",
                    "confidence": 90,
                    "severity": "high",
                    "created_at": datetime.now(timezone.utc) - timedelta(days=45),
                },
                {
                    "server_id": "srv-2",
                    "source": "beta",
                    "pulse_id": "pulse-2",
                    "pulse_name": "Pulse Two",
                    "confidence": 80,
                    "severity": "medium",
                    "created_at": datetime.now(timezone.utc),
                },
            ],
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session() -> Iterable[Session]:
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat/coverage")
    def coverage(
        stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_threat_coverage(db, stale_after_days)

    response = TestClient(that_app).get("/api/threat/coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["servers_with_associations"] == 2
    assert payload["coverage_pct"] == round(2 / 3 * 100, 2)
    assert len(payload["by_source"]) == 2
    assert all(isinstance(entry["servers"], int) for entry in payload["by_source"])
    assert all(isinstance(entry["pulses"], int) for entry in payload["by_source"])
    print("PASS")


if __name__ == "__main__":
    _self_test()