from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef

DANGEROUS_RISK_TIERS = ("HIGH_RISK_ISOLATED", "KNOWN_THREAT")


def _iso_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat()
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return candidate
    return str(value)


def compute_linkage(
    db: Session,
    days: int = 30,
    indicator_type: str | None = None,
) -> dict[str, Any]:
    """Compute threat-intelligence linkage buckets from the application database."""
    normalized_days = max(0, int(days))
    cutoff = datetime.utcnow() - timedelta(days=normalized_days)

    conditions = ["pulse_created >= :cutoff"]
    parameters: dict[str, Any] = {"cutoff": cutoff}
    if indicator_type is not None:
        conditions.append("indicator_type = :indicator_type")
        parameters["indicator_type"] = indicator_type

    sql = text(
        f"""
        WITH filtered_refs AS (
            SELECT
                indicator_type,
                is_aggregator,
                indicator_value,
                pulse_id,
                pulse_created
            FROM threat_intel_refs
            WHERE {' AND '.join(conditions)}
        ),
        bucket_stats AS (
            SELECT
                indicator_type,
                is_aggregator,
                COUNT(DISTINCT indicator_value) AS indicators,
                COUNT(DISTINCT pulse_id) AS pulses,
                MAX(pulse_created) AS newest_pulse
            FROM filtered_refs
            GROUP BY indicator_type, is_aggregator
        ),
        totals AS (
            SELECT COUNT(DISTINCT indicator_value) AS total_indicators
            FROM filtered_refs
        ),
        risky_servers AS (
            SELECT COUNT(DISTINCT server_id) AS at_risk_servers
            FROM mcp_server_registry
            WHERE risk_tier IN ('HIGH_RISK_ISOLATED', 'KNOWN_THREAT')
        )
        SELECT
            bucket_stats.indicator_type,
            bucket_stats.is_aggregator,
            bucket_stats.indicators,
            bucket_stats.pulses,
            bucket_stats.newest_pulse,
            risky_servers.at_risk_servers,
            totals.total_indicators
        FROM bucket_stats
        CROSS JOIN risky_servers
        CROSS JOIN totals
        ORDER BY bucket_stats.indicator_type, bucket_stats.is_aggregator
        """
    )

    rows = db.execute(sql, parameters).mappings().all()

    buckets: list[dict[str, Any]] = []
    total_indicators = 0
    for row in rows:
        total_indicators = int(row["total_indicators"] or 0)
        buckets.append(
            {
                "indicator_type": str(row["indicator_type"]),
                "is_aggregator": bool(row["is_aggregator"]),
                "indicators": int(row["indicators"] or 0),
                "pulses": int(row["pulses"] or 0),
                "at_risk_servers": int(row["at_risk_servers"] or 0),
                "newest_pulse": _iso_string(row["newest_pulse"]),
            }
        )

    return {
        "days": normalized_days,
        "buckets": buckets,
        "total_indicators": total_indicators,
    }


compute_threat_intel_linkage = compute_linkage
get_threat_intel_linkage = compute_linkage
build_threat_intel_linkage = compute_linkage
get_linkage = compute_linkage
build_linkage = compute_linkage


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
    ThreatIntelRef.__table__.create(test_engine)
    McpServerRegistry.__table__.create(test_engine)

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    now = datetime.utcnow()

    with TestSession() as session:
        session.execute(
            text(
                """
                INSERT INTO threat_intel_refs (
                    id, indicator_type, indicator_value, pulse_id, pulse_name,
                    pulse_created, is_aggregator, source, source_url, fetched_at
                ) VALUES (
                    :id, :indicator_type, :indicator_value, :pulse_id, :pulse_name,
                    :pulse_created, :is_aggregator, :source, :source_url, :fetched_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "indicator_type": "ipv4",
                    "indicator_value": "192.0.2.1",
                    "pulse_id": "pulse-1",
                    "pulse_name": "Pulse 1",
                    "pulse_created": now - timedelta(days=2),
                    "is_aggregator": False,
                    "source": "test",
                    "source_url": "https://example.test/1",
                    "fetched_at": now,
                },
                {
                    "id": 2,
                    "indicator_type": "ipv4",
                    "indicator_value": "192.0.2.2",
                    "pulse_id": "pulse-2",
                    "pulse_name": "Pulse 2",
                    "pulse_created": now - timedelta(days=1),
                    "is_aggregator": False,
                    "source": "test",
                    "source_url": "https://example.test/2",
                    "fetched_at": now,
                },
                {
                    "id": 3,
                    "indicator_type": "ipv4",
                    "indicator_value": "192.0.2.3",
                    "pulse_id": "pulse-3",
                    "pulse_name": "Pulse 3",
                    "pulse_created": now - timedelta(hours=1),
                    "is_aggregator": False,
                    "source": "test",
                    "source_url": "https://example.test/3",
                    "fetched_at": now,
                },
                {
                    "id": 4,
                    "indicator_type": "domain",
                    "indicator_value": "one.example.test",
                    "pulse_id": "pulse-4",
                    "pulse_name": "Pulse 4",
                    "pulse_created": now - timedelta(days=3),
                    "is_aggregator": True,
                    "source": "test",
                    "source_url": "https://example.test/4",
                    "fetched_at": now,
                },
                {
                    "id": 5,
                    "indicator_type": "domain",
                    "indicator_value": "two.example.test",
                    "pulse_id": "pulse-5",
                    "pulse_name": "Pulse 5",
                    "pulse_created": now - timedelta(days=2),
                    "is_aggregator": True,
                    "source": "test",
                    "source_url": "https://example.test/5",
                    "fetched_at": now,
                },
                {
                    "id": 6,
                    "indicator_type": "domain",
                    "indicator_value": "three.example.test",
                    "pulse_id": "pulse-6",
                    "pulse_name": "Pulse 6",
                    "pulse_created": now - timedelta(hours=2),
                    "is_aggregator": True,
                    "source": "test",
                    "source_url": "https://example.test/6",
                    "fetched_at": now,
                },
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, risk_tier, trust_score, verdict
                ) VALUES (
                    :server_id, :name, :risk_tier, :trust_score, :verdict
                )
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "Risky One",
                    "risk_tier": "HIGH_RISK_ISOLATED",
                    "trust_score": 10.0,
                    "verdict": "block",
                },
                {
                    "server_id": "server-2",
                    "name": "Risky Two",
                    "risk_tier": "KNOWN_THREAT",
                    "trust_score": 15.0,
                    "verdict": "block",
                },
                {
                    "server_id": "server-3",
                    "name": "Safe One",
                    "risk_tier": "LOW_RISK",
                    "trust_score": 90.0,
                    "verdict": "allow",
                },
                {
                    "server_id": "server-4",
                    "name": "Safe Two",
                    "risk_tier": "MEDIUM_RISK",
                    "trust_score": 70.0,
                    "verdict": "review",
                },
            ],
        )
        session.commit()

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/linkage")
    def linkage_endpoint(
        days: int = 30,
        indicator_type: str | None = None,
        db: Session = Depends(get_session),
    ):
        return compute_linkage(db=db, days=days, indicator_type=indicator_type)

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threat-intel/linkage",
        params={"days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_indicators"] == 6
    assert len(payload["buckets"]) == 2
    ipv4_bucket = next(
        bucket for bucket in payload["buckets"] if bucket["indicator_type"] == "ipv4"
    )
    assert ipv4_bucket["at_risk_servers"] == 2
    assert isinstance(ipv4_bucket["newest_pulse"], str)
    assert ipv4_bucket["newest_pulse"]
    datetime.fromisoformat(ipv4_bucket["newest_pulse"])
    print("PASS")