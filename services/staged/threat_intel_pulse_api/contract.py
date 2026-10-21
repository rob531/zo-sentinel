from datetime import datetime, timezone
from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef


class ThreatIntelPulse(BaseModel):
    pulse_id: str
    pulse_name: str
    indicator_count: int
    indicator_types: List[str]
    flagged_server_count: int
    aggregator: bool
    source: str
    latest_fetched_at: str


class ThreatIntelPulseList(BaseModel):
    pulses: List[ThreatIntelPulse]


router = APIRouter(prefix="/api", tags=["threat-intel"])


def _iso8601(value: Optional[datetime]) -> str:
    if value is None:
        return "1970-01-01T00:00:00Z"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_pulse_rollup(
    session: Session,
    limit: int = 50,
) -> List[ThreatIntelPulse]:
    pulse_ids = (
        select(ThreatIntelRef.pulse_id)
        .where(ThreatIntelRef.pulse_id.is_not(None))
        .group_by(ThreatIntelRef.pulse_id)
        .order_by(func.max(ThreatIntelRef.fetched_at).desc())
        .limit(limit)
    )

    rows = session.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
            ThreatIntelRef.fetched_at,
        ).where(ThreatIntelRef.pulse_id.in_(pulse_ids))
    ).all()

    flagged_server_count = int(
        session.execute(
            select(func.count(distinct(McpServerRegistry.server_id))).where(
                McpServerRegistry.server_id.is_not(None),
                McpServerRegistry.risk_tier.is_not(None),
                func.upper(McpServerRegistry.risk_tier) != "LOW",
            )
        ).scalar_one()
    )

    grouped = {}
    for row in rows:
        pulse_id = str(row.pulse_id)
        pulse = grouped.setdefault(
            pulse_id,
            {
                "pulse_name": str(row.pulse_name or ""),
                "indicator_types": set(),
                "indicator_count": 0,
                "aggregator": False,
                "source": str(row.source or ""),
                "latest_fetched_at": None,
                "latest_key": None,
            },
        )
        pulse["indicator_count"] += 1
        if row.indicator_type is not None:
            pulse["indicator_types"].add(str(row.indicator_type))
        pulse["aggregator"] = pulse["aggregator"] or bool(row.is_aggregator)

        fetched_at = row.fetched_at
        if fetched_at is not None:
            if fetched_at.tzinfo is None:
                fetched_key = fetched_at.replace(tzinfo=timezone.utc)
            else:
                fetched_key = fetched_at.astimezone(timezone.utc)
            if (
                pulse["latest_key"] is None
                or fetched_key > pulse["latest_key"]
            ):
                pulse["latest_key"] = fetched_key
                pulse["latest_fetched_at"] = fetched_at
                if row.source is not None:
                    pulse["source"] = str(row.source)

    pulses = [
        ThreatIntelPulse(
            pulse_id=pulse_id,
            pulse_name=pulse["pulse_name"],
            indicator_count=pulse["indicator_count"],
            indicator_types=sorted(pulse["indicator_types"]),
            flagged_server_count=flagged_server_count,
            aggregator=pulse["aggregator"],
            source=pulse["source"],
            latest_fetched_at=_iso8601(pulse["latest_fetched_at"]),
        )
        for pulse_id, pulse in grouped.items()
    ]
    pulses.sort(
        key=lambda pulse: (
            pulse.latest_fetched_at,
            pulse.pulse_id,
        ),
        reverse=True,
    )
    return pulses


@router.get(
    "/threatintel/pulses",
    response_model=ThreatIntelPulseList,
)
def list_threat_intel_pulses(
    limit: int = Query(default=50, ge=1, le=500),
    aggregator_only: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> ThreatIntelPulseList:
    pulses = build_pulse_rollup(session, limit=limit)
    if aggregator_only:
        pulses = [pulse for pulse in pulses if pulse.aggregator]
    return ThreatIntelPulseList(pulses=pulses)


def run() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    indicator_type VARCHAR,
                    indicator_value VARCHAR,
                    pulse_id VARCHAR,
                    pulse_name VARCHAR,
                    pulse_created DATETIME,
                    is_aggregator BOOLEAN,
                    source VARCHAR,
                    source_url VARCHAR,
                    fetched_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    trust_score FLOAT,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs (
                    indicator_type,
                    indicator_value,
                    pulse_id,
                    pulse_name,
                    pulse_created,
                    is_aggregator,
                    source,
                    source_url,
                    fetched_at
                ) VALUES (
                    :indicator_type,
                    :indicator_value,
                    :pulse_id,
                    :pulse_name,
                    :pulse_created,
                    :is_aggregator,
                    :source,
                    :source_url,
                    :fetched_at
                )
                """
            ),
            [
                {
                    "indicator_type": "ip",
                    "indicator_value": "198.51.100.10",
                    "pulse_id": "pulse-aggregated",
                    "pulse_name": "Aggregated Pulse",
                    "pulse_created": "2025-01-02T00:00:00Z",
                    "is_aggregator": True,
                    "source": "internal-aggregator",
                    "source_url": "https://intel.example/pulse-aggregated",
                    "fetched_at": "2025-01-03T00:00:00Z",
                },
                {
                    "indicator_type": "domain",
                    "indicator_value": "example.test",
                    "pulse_id": "pulse-aggregated",
                    "pulse_name": "Aggregated Pulse",
                    "pulse_created": "2025-01-02T00:00:00Z",
                    "is_aggregator": True,
                    "source": "internal-aggregator",
                    "source_url": "https://intel.example/pulse-aggregated",
                    "fetched_at": "2025-01-03T01:00:00Z",
                },
                {
                    "indicator_type": "hash",
                    "indicator_value": "abc123",
                    "pulse_id": "pulse-direct",
                    "pulse_name": "Direct Pulse",
                    "pulse_created": "2025-01-01T00:00:00Z",
                    "is_aggregator": False,
                    "source": "osint",
                    "source_url": "https://intel.example/pulse-direct",
                    "fetched_at": "2025-01-02T00:00:00Z",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    risk_tier,
                    verdict,
                    trust_score,
                    last_assessed
                ) VALUES (
                    :server_id,
                    :name,
                    :risk_tier,
                    :verdict,
                    :trust_score,
                    :last_assessed
                )
                """
            ),
            [
                {
                    "server_id": "high-risk-server",
                    "name": "High Risk Server",
                    "risk_tier": "HIGH_RISK_ISOLATED",
                    "verdict": "isolate",
                    "trust_score": 0.1,
                    "last_assessed": "2025-01-03T00:00:00Z",
                },
                {
                    "server_id": "low-risk-server",
                    "name": "Low Risk Server",
                    "risk_tier": "LOW",
                    "verdict": "approve",
                    "trust_score": 0.9,
                    "last_assessed": "2025-01-03T00:00:00Z",
                },
            ],
        )

    session = Session(engine)
    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Iterator[Session]:
        yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/threatintel/pulses", params={"limit": 10})
            assert response.status_code == 200, response.text
            pulses = response.json()["pulses"]
            assert len(pulses) == 2, pulses

            aggregator_pulse = next(
                pulse for pulse in pulses if pulse["pulse_id"] == "pulse-aggregated"
            )
            assert aggregator_pulse["aggregator"] is True, aggregator_pulse
            assert aggregator_pulse["flagged_server_count"] == 1, aggregator_pulse

            filtered_response = client.get(
                "/api/threatintel/pulses",
                params={"limit": 10, "aggregator_only": "true"},
            )
            assert filtered_response.status_code == 200, filtered_response.text
            filtered_pulses = filtered_response.json()["pulses"]
            assert len(filtered_pulses) == 1, filtered_pulses
            assert filtered_pulses[0]["pulse_id"] == "pulse-aggregated"
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()