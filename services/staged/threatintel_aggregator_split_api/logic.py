from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _clamp_days(days: int) -> int:
    return max(1, min(365, int(days)))


def get_aggregator_split(session: Session, days: int = 30) -> Dict[str, Any]:
    effective_days = _clamp_days(days)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=effective_days)

    source_rows = session.execute(
        text(
            """
            SELECT
                source,
                COUNT(*) AS total,
                SUM(CASE WHEN is_aggregator = TRUE THEN 1 ELSE 0 END) AS aggregator_count,
                SUM(CASE WHEN is_aggregator = FALSE THEN 1 ELSE 0 END) AS direct_count
            FROM threat_intel_refs
            WHERE fetched_at >= :cutoff
              AND fetched_at <= :now
            GROUP BY source
            ORDER BY source
            """
        ),
        {"cutoff": cutoff, "now": now},
    ).mappings()

    aggregators: List[Dict[str, Any]] = []
    direct: List[Dict[str, Any]] = []
    total = 0
    aggregator_count = 0

    for row in source_rows:
        row_total = int(row["total"] or 0)
        row_aggregator_count = int(row["aggregator_count"] or 0)
        row_direct_count = int(row["direct_count"] or 0)

        total += row_total
        aggregator_count += row_aggregator_count

        if row_aggregator_count:
            aggregators.append(
                {"source": row["source"], "count": row_aggregator_count}
            )
        if row_direct_count:
            direct.append({"source": row["source"], "count": row_direct_count})

    indicator_rows = session.execute(
        text(
            """
            SELECT
                indicator_type,
                COUNT(*) AS total,
                SUM(CASE WHEN is_aggregator = TRUE THEN 1 ELSE 0 END) AS aggregator_count,
                SUM(CASE WHEN is_aggregator = FALSE THEN 1 ELSE 0 END) AS direct_count
            FROM threat_intel_refs
            WHERE fetched_at >= :cutoff
              AND fetched_at <= :now
            GROUP BY indicator_type
            ORDER BY indicator_type
            """
        ),
        {"cutoff": cutoff, "now": now},
    ).mappings()

    by_indicator_type = [
        {
            "indicator_type": row["indicator_type"],
            "total": int(row["total"] or 0),
            "aggregator_count": int(row["aggregator_count"] or 0),
            "direct_count": int(row["direct_count"] or 0),
        }
        for row in indicator_rows
    ]

    aggregator_ratio = round(aggregator_count / total, 4) if total else 0.0

    return {
        "days": effective_days,
        "aggregators": aggregators,
        "direct": direct,
        "total": total,
        "aggregator_count": aggregator_count,
        "aggregator_ratio": aggregator_ratio,
        "by_indicator_type": by_indicator_type,
    }


router = APIRouter(prefix="/api")


@router.get("/threat-intel/aggregator-split")
def aggregator_split(
    days: int = Query(30),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_aggregator_split(session, days=days)


app = FastAPI()
app.include_router(router)


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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as test_session:
        test_session.execute(
            text(
                """
                INSERT INTO threat_intel_refs
                    (id, indicator_type, indicator_value, pulse_id, pulse_name,
                     pulse_created, is_aggregator, source, source_url, fetched_at)
                VALUES
                    (1, 'ip', '192.0.2.1', 'pulse-1', 'Pulse 1',
                     :pulse_created, :aggregator_1, 'source-a', 'https://source-a/1', :fetched_1),
                    (2, 'domain', 'example.com', 'pulse-1', 'Pulse 1',
                     :pulse_created, :aggregator_2, 'source-a', 'https://source-a/2', :fetched_2),
                    (3, 'ip', '198.51.100.1', 'pulse-2', 'Pulse 2',
                     :pulse_created, :direct, 'source-b', 'https://source-b/1', :fetched_3),
                    (4, 'ip', '203.0.113.1', 'pulse-3', 'Pulse 3',
                     :pulse_created, :direct, 'source-c', 'https://source-c/1', :old)
                """
            ),
            {
                "pulse_created": now - timedelta(days=2),
                "aggregator_1": True,
                "aggregator_2": True,
                "direct": False,
                "fetched_1": now - timedelta(days=1),
                "fetched_2": now - timedelta(days=2),
                "fetched_3": now - timedelta(days=3),
                "old": now - timedelta(days=40),
            },
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get(
        "/api/threat-intel/aggregator-split", params={"days": 30}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert payload["total"] == 3
    assert payload["aggregator_count"] == 2
    assert payload["aggregator_ratio"] == 0.6667
    assert payload["aggregators"] == [{"source": "source-a", "count": 2}]
    assert payload["direct"] == [{"source": "source-b", "count": 1}]
    assert payload["by_indicator_type"] == [
        {"indicator_type": "domain", "total": 1, "aggregator_count": 1, "direct_count": 0},
        {"indicator_type": "ip", "total": 2, "aggregator_count": 1, "direct_count": 1},
    ]
    print("PASS")