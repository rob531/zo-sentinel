"""Logic for threat-intelligence coverage reporting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, inspect, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, ThreatIntelRef


_ASSOCIATION_TABLE = "mcp_threat_associations"


def _timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _association_rows(
    db: Session, server_ids: Set[str]
) -> List[Tuple[str, str]]:
    """Read the association table through SQLAlchemy metadata.

    The association table is not represented by an app model, so it is
    reflected from the bound database when it is present.
    """
    if not server_ids:
        return []

    bind = db.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(_ASSOCIATION_TABLE):
        return []

    metadata = MetaData()
    association = Table(_ASSOCIATION_TABLE, metadata, autoload_with=bind)
    columns = {column.name for column in association.columns}
    indicator_column = "indicator" if "indicator" in columns else "threat_id"
    if indicator_column not in columns or "server_id" not in columns:
        return []

    rows = db.execute(
        select(
            association.c.server_id,
            association.c[indicator_column],
        ).where(association.c.server_id.in_(server_ids))
    ).all()

    result: List[Tuple[str, str]] = []
    for server_id, indicator in rows:
        if server_id is None or indicator is None:
            continue
        result.append((str(server_id), str(indicator)))
    return result


def _ref_keys(ref: ThreatIntelRef) -> Set[str]:
    keys: Set[str] = set()
    if ref.id is not None:
        keys.add(str(ref.id))
    if ref.indicator_value is not None:
        keys.add(str(ref.indicator_value))
    return keys


def get_coverage(db: Session) -> Dict[str, Any]:
    """Compute source-level threat-intelligence coverage for the registry."""
    refs = list(db.execute(select(ThreatIntelRef)).scalars().all())
    servers = list(
        db.execute(select(McpServerRegistry.server_id)).scalars().all()
    )
    server_ids = {str(server_id) for server_id in servers if server_id is not None}

    source_refs: Dict[str, List[ThreatIntelRef]] = {}
    for ref in refs:
        source = str(ref.source) if ref.source is not None else "unknown"
        source_refs.setdefault(source, []).append(ref)

    associations = _association_rows(db, server_ids)
    linked_by_source: Dict[str, Set[str]] = {
        source: set() for source in source_refs
    }
    linked_servers: Set[str] = set()
    coverage_indicators: Set[str] = set()

    refs_by_key: Dict[str, List[ThreatIntelRef]] = {}
    for ref in refs:
        for key in _ref_keys(ref):
            refs_by_key.setdefault(key, []).append(ref)

    for server_id, indicator_key in associations:
        matching_refs = refs_by_key.get(indicator_key, [])
        if not matching_refs:
            continue

        linked_servers.add(server_id)
        for ref in matching_refs:
            source = str(ref.source) if ref.source is not None else "unknown"
            linked_by_source.setdefault(source, set()).add(server_id)
            if ref.indicator_value is not None:
                coverage_indicators.add(str(ref.indicator_value))

    now = datetime.now(timezone.utc)
    sources: List[Dict[str, Any]] = []
    for source, source_items in source_refs.items():
        latest: Optional[datetime] = None
        for ref in source_items:
            fetched_at = _timestamp(ref.fetched_at)
            if fetched_at is not None and (latest is None or fetched_at > latest):
                latest = fetched_at

        staleness_hours: Optional[float] = None
        if latest is not None:
            staleness_hours = max(
                0.0, (now - latest).total_seconds() / 3600.0
            )

        sources.append(
            {
                "source": source,
                "indicator_count": len(source_items),
                "distinct_pulses": len(
                    {
                        str(ref.pulse_id)
                        for ref in source_items
                        if ref.pulse_id is not None
                    }
                ),
                "linked_servers": len(linked_by_source.get(source, set())),
                "staleness_hours": staleness_hours,
                "latest_fetched_at": _iso(latest),
            }
        )

    sources.sort(key=lambda item: item["source"])

    total_pulses = len(
        {
            str(ref.pulse_id)
            for ref in refs
            if ref.pulse_id is not None
        }
    )

    return {
        "sources": sources,
        "totals": {
            "indicators": len(refs),
            "sources": len(source_refs),
            "pulses": total_pulses,
            "linked_servers": len(linked_servers),
        },
        "generated_at": _iso(now),
    }


def get_coverage_report(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the report naming convention."""
    return get_coverage(db)


def compute_coverage(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the computation naming convention."""
    return get_coverage(db)


def get_threat_intel_coverage(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the service-specific name."""
    return get_coverage(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    ThreatIntelRef.__table__.create(engine)

    association_metadata = MetaData()
    Table(
        _ASSOCIATION_TABLE,
        association_metadata,
        Column("server_id", String, primary_key=True),
        Column("threat_id", String, nullable=False),
        Column("severity", String),
        Column("confidence", Integer),
    ).create(engine)

    session = session_factory()
    session.add(
        McpServerRegistry(server_id="server-a", name="Server A", risk_tier="low")
    )
    session.add(
        McpServerRegistry(server_id="server-b", name="Server B", risk_tier="high")
    )
    session.add(
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            source="alpha",
            source_url="https://alpha.example",
        )
    )
    session.add(
        ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            source="alpha",
            source_url="https://alpha.example",
        )
    )
    session.add(
        ThreatIntelRef(
            id=3,
            indicator_type="ip",
            indicator_value="198.51.100.4",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            source="beta",
            source_url="https://beta.example",
        )
    )
    session.execute(
        association_metadata.tables[_ASSOCIATION_TABLE].insert(),
        [
            {
                "server_id": "server-a",
                "threat_id": "192.0.2.1",
                "severity": "high",
                "confidence": 90,
            },
            {
                "server_id": "server-b",
                "threat_id": "3",
                "severity": "medium",
                "confidence": 70,
            },
        ],
    )
    session.commit()

    def override_get_session():
        yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threatintel/coverage")
    def coverage_route(db: Session = Depends(get_session)):
        return get_coverage(db)

    result = get_coverage(session)
    assert len(result["sources"]) == 2
    assert any(
        source["source"] == "alpha" and source["indicator_count"] == 2
        for source in result["sources"]
    )
    assert any(
        source["source"] == "alpha" and source["linked_servers"] == 1
        for source in result["sources"]
    )
    assert result["totals"]["linked_servers"] == 2
    print("PASS")
    session.close()
    engine.dispose()