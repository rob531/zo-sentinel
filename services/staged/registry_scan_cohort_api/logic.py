from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


COHORT_ORDER = (
    "never_scanned",
    "scanned_once",
    "scanned_2_5",
    "scanned_6_plus",
)


def _cohort_for_scan_count(scan_count: Optional[int]) -> str:
    if scan_count is None or scan_count == 0:
        return "never_scanned"
    if scan_count == 1:
        return "scanned_once"
    if 2 <= scan_count <= 5:
        return "scanned_2_5"
    return "scanned_6_plus"


def get_scan_cohorts(
    session: Session,
    registry_source: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    statement = select(
        McpServerRegistry.scan_count,
        McpServerRegistry.risk_tier,
        McpServerRegistry.registry_source,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = session.execute(statement).all()

    cohorts: Dict[str, List[Optional[int]]] = {
        cohort: [] for cohort in COHORT_ORDER
    }
    risk_tiers = set()
    source_counts: Dict[str, int] = {}

    for scan_count, risk_tier, source in rows:
        cohort = _cohort_for_scan_count(scan_count)
        cohorts[cohort].append(scan_count)

        if risk_tier is not None:
            risk_tiers.add(risk_tier)

        source_name = "" if source is None else str(source)
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

    cohort_results = []
    for cohort in COHORT_ORDER:
        values = [
            0 if value is None else value
            for value in cohorts[cohort]
        ]
        cohort_results.append(
            {
                "cohort": cohort,
                "servers": len(values),
                "distinct_risk_tiers": len(
                    {
                        row.risk_tier
                        for row in rows
                        if row.risk_tier is not None
                        and _cohort_for_scan_count(row.scan_count) == cohort
                    }
                ),
                "median_scan_count": (
                    float(median(values)) if values else None
                ),
            }
        )

    source_limit = max(0, int(limit))
    sources = [
        {"registry_source": source, "servers": count}
        for source, count in sorted(source_counts.items())
    ][:source_limit]

    return {
        "total_servers": len(rows),
        "cohorts": cohort_results,
        "sources": sources,
    }


def get_scan_cohort_data(
    session: Session,
    registry_source: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    return get_scan_cohorts(session, registry_source, limit)


def get_registry_scan_cohorts(
    session: Session,
    registry_source: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    return get_scan_cohorts(session, registry_source, limit)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine)

    test_session = test_session_factory()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="s1",
                name="one",
                registry_source="alpha",
                scan_count=None,
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="s2",
                name="two",
                registry_source="alpha",
                scan_count=0,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="s3",
                name="three",
                registry_source="beta",
                scan_count=1,
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="s4",
                name="four",
                registry_source="beta",
                scan_count=3,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="s5",
                name="five",
                registry_source="beta",
                scan_count=4,
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="s6",
                name="six",
                registry_source="beta",
                scan_count=9,
                risk_tier="low",
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-cohorts")
    def scan_cohorts(session: Session = Depends(get_session)):
        return get_scan_cohorts(session)

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/registry/scan-cohorts")
    payload = response.json()

    assert response.status_code == 200
    assert len(payload["cohorts"]) == 4
    assert payload["total_servers"] == 6
    never_scanned = next(
        item for item in payload["cohorts"] if item["cohort"] == "never_scanned"
    )
    assert never_scanned["servers"] == 2

    test_session.close()
    test_engine.dispose()
    print("PASS")