"""logic.py -- threat intelligence reference coverage computation.

Reads the authoritative threat_intel_refs table through the application ORM and
returns per-indicator coverage plus the most recently created references.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_ref_coverage(db: Session) -> Dict[str, Any]:
    """Compute reference coverage from the real threat_intel_refs table."""
    total_refs = int(
        db.execute(
            select(func.count()).select_from(ThreatIntelRef)
        ).scalar_one()
    )

    aggregate_rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label("count"),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ).label("aggregator_count"),
            func.max(ThreatIntelRef.pulse_created).label("latest_pulse_created"),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    by_type: List[Dict[str, Any]] = [
        {
            "indicator_type": indicator_type,
            "count": int(count),
            "aggregator_count": int(aggregator_count or 0),
            "latest_pulse_created": latest_pulse_created,
            "latest_fetched_at": latest_fetched_at,
        }
        for indicator_type, count, aggregator_count, latest_pulse_created, latest_fetched_at in aggregate_rows
    ]

    # The explicit null rank keeps ordering consistent between PostgreSQL and
    # SQLite, whose default null ordering differs.
    latest_rows = db.execute(
        select(ThreatIntelRef)
        .order_by(
            case((ThreatIntelRef.pulse_created.is_(None), 1), else_=0),
            desc(ThreatIntelRef.pulse_created),
            desc(ThreatIntelRef.id),
        )
        .limit(20)
    ).scalars().all()

    latest = [
        {
            "id": ref.id,
            "indicator_type": ref.indicator_type,
            "indicator_value": ref.indicator_value,
            "pulse_id": ref.pulse_id,
            "pulse_name": ref.pulse_name,
            "pulse_created": ref.pulse_created,
            "is_aggregator": ref.is_aggregator,
            "source": ref.source,
            "source_url": ref.source_url,
            "fetched_at": ref.fetched_at,
        }
        for ref in latest_rows
    ]

    return {
        "total_refs": total_refs,
        "by_type": by_type,
        "latest": latest,
    }


def get_coverage(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the service's short name."""
    return get_ref_coverage(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    seed_rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ipv4",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse 1",
            pulse_created=datetime(2024, 1, 1, 12, 0, 0),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/1",
            fetched_at=datetime(2024, 1, 1, 12, 5, 0),
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ipv4",
            indicator_value="192.0.2.2",
            pulse_id="pulse-2",
            pulse_name="Pulse 2",
            pulse_created=datetime(2024, 1, 2, 12, 0, 0),
            is_aggregator=True,
            source="test",
            source_url="https://example.test/2",
            fetched_at=datetime(2024, 1, 2, 12, 5, 0),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-3",
            pulse_name="Pulse 3",
            pulse_created=datetime(2024, 1, 3, 12, 0, 0),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/3",
            fetched_at=datetime(2024, 1, 3, 12, 5, 0),
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="other.test",
            pulse_id="pulse-4",
            pulse_name="Pulse 4",
            pulse_created=datetime(2024, 1, 4, 12, 0, 0),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/4",
            fetched_at=datetime(2024, 1, 4, 12, 5, 0),
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="ipv4",
            indicator_value="192.0.2.3",
            pulse_id="pulse-5",
            pulse_name="Pulse 5",
            pulse_created=datetime(2024, 1, 5, 12, 0, 0),
            is_aggregator=False,
            source="test",
            source_url="https://example.test/5",
            fetched_at=datetime(2024, 1, 5, 12, 5, 0),
        ),
    ]

    with TestSession() as db:
        db.add_all(seed_rows)
        db.commit()

        def override_get_session():
            with TestSession() as session:
                yield session

        that_app = FastAPI()

        @that_app.get("/api/threat-intel/refs/coverage")
        def coverage(session: Session = Depends(get_session)):
            return get_ref_coverage(session)

        that_app.dependency_overrides[get_session] = override_get_session
        response = TestClient(that_app).get("/api/threat-intel/refs/coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_refs"] == 5
        assert len(payload["by_type"]) == 2
        assert len(payload["latest"]) == 5
        ipv4 = next(
            row for row in payload["by_type"] if row["indicator_type"] == "ipv4"
        )
        assert ipv4["aggregator_count"] == 1

    print("PASS")