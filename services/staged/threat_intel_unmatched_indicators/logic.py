"""logic.py -- the service's data/computation layer (one concern, one file).

Reads threat intelligence references and the authoritative MCP server registry
from the real application data layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef


def get_unmatched_indicators(db: Session, limit: int = 20) -> Dict[str, Any]:
    """Return indicator totals and unmatched indicator values.

    A reference is unmatched when its indicator value is neither the URL nor
    the name of a row in ``mcp_server_registry``.
    """
    limit = max(0, min(int(limit), 20))

    total_rows = db.execute(
        select(ThreatIntelRef.indicator_type, func.count(ThreatIntelRef.id))
        .group_by(ThreatIntelRef.indicator_type)
    ).all()

    unmatched_rows = db.execute(
        select(ThreatIntelRef.indicator_type, func.count(ThreatIntelRef.id))
        .outerjoin(
            McpServerRegistry,
            or_(
                McpServerRegistry.url == ThreatIntelRef.indicator_value,
                McpServerRegistry.name == ThreatIntelRef.indicator_value,
            ),
        )
        .where(McpServerRegistry.server_id.is_(None))
        .group_by(ThreatIntelRef.indicator_type)
    ).all()

    totals = {str(indicator_type): int(total) for indicator_type, total in total_rows}
    unmatched_by_type = {
        str(indicator_type): int(unmatched) for indicator_type, unmatched in unmatched_rows
    }

    by_type: List[Dict[str, Any]] = [
        {
            "indicator_type": indicator_type,
            "total": total,
            "unmatched": unmatched_by_type.get(indicator_type, 0),
        }
        for indicator_type, total in totals.items()
    ]
    by_type.sort(key=lambda item: item["indicator_type"])

    recent_rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.source,
            ThreatIntelRef.fetched_at,
        )
        .outerjoin(
            McpServerRegistry,
            or_(
                McpServerRegistry.url == ThreatIntelRef.indicator_value,
                McpServerRegistry.name == ThreatIntelRef.indicator_value,
            ),
        )
        .where(McpServerRegistry.server_id.is_(None))
        .order_by(
            ThreatIntelRef.fetched_at.desc(),
            ThreatIntelRef.id.desc(),
        )
        .limit(limit)
    ).all()

    recent_unmatched = [
        {
            "indicator_type": str(indicator_type),
            "indicator_value": indicator_value,
            "source": source,
            "fetched_at": fetched_at,
        }
        for indicator_type, indicator_value, source, fetched_at in recent_rows
    ]

    return {
        "total_indicators": sum(totals.values()),
        "by_type": by_type,
        "recent_unmatched": recent_unmatched,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# Compatibility names for callers that use a report-oriented function name.
def get_unmatched_indicator_report(db: Session, limit: int = 20) -> Dict[str, Any]:
    return get_unmatched_indicators(db, limit=limit)


def get_threat_intel_unmatched_indicators(
    db: Session, limit: int = 20
) -> Dict[str, Any]:
    return get_unmatched_indicators(db, limit=limit)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    ThreatIntelRef.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    test_db = SessionLocal()

    try:
        test_db.add(
            McpServerRegistry(
                server_id="server-1",
                name="known-server",
                url="https://known.example",
                description="test server",
                registry_source="test",
                risk_tier="low",
                trust_score=50,
                confidence=1.0,
                verdict="trusted",
            )
        )
        test_db.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="url",
                    indicator_value="https://unknown.example",
                    pulse_id="pulse-1",
                    pulse_name="pulse",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://source.example/1",
                    fetched_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="url",
                    indicator_value="known-server",
                    pulse_id="pulse-1",
                    pulse_name="pulse",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://source.example/2",
                    fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="https://unknown.example",
                    pulse_id="pulse-2",
                    pulse_name="pulse",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://source.example/3",
                    fetched_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        test_db.commit()

        def override_get_session():
            yield test_db

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = override_get_session

        @that_app.get("/api/threat-intel/unmatched-indicators")
        def unmatched_endpoint(limit: int = 20, db: Session = Depends(get_session)):
            return get_unmatched_indicators(db, limit=limit)

        from fastapi.testclient import TestClient

        response = TestClient(that_app).get(
            "/api/threat-intel/unmatched-indicators", params={"limit": 20}
        )
        assert response.status_code == 200
        payload = response.json()
        totals = {item["indicator_type"]: item["total"] for item in payload["by_type"]}
        unmatched = {
            item["indicator_type"]: item["unmatched"] for item in payload["by_type"]
        }
        assert totals.get("url") == 2
        assert totals.get("domain") == 1
        assert unmatched.get("url", 0) >= 1
        assert payload["recent_unmatched"]
        print("PASS")
    finally:
        test_db.close()
        engine.dispose()