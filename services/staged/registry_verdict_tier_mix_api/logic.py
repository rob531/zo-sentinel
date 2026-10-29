from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpServerRegistry
except ImportError:
    try:
        from app.models import McpServerRegistry as McpServerRegistry
    except ImportError:
        from app.models import McpServerRegistry as McpServerRegistry


def get_verdict_tier_mix(
    session: Session = Depends(get_session),
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the fleet-wide registry distribution by verdict and risk tier."""
    where_clause = ""
    params: Dict[str, Any] = {}

    if source is not None:
        where_clause = "WHERE registry_source = :source"
        params["source"] = source

    sql = f"""
        SELECT
            CASE
                WHEN verdict IS NULL THEN 'UNSCORED'
                ELSE verdict
            END AS verdict,
            CASE
                WHEN verdict IS NULL OR risk_tier IS NULL THEN 'UNSCORED'
                ELSE risk_tier
            END AS risk_tier,
            COUNT(*) AS bucket_count
        FROM mcp_server_registry
        {where_clause}
        GROUP BY
            CASE
                WHEN verdict IS NULL THEN 'UNSCORED'
                ELSE verdict
            END,
            CASE
                WHEN verdict IS NULL OR risk_tier IS NULL THEN 'UNSCORED'
                ELSE risk_tier
            END
        ORDER BY verdict ASC, risk_tier ASC
    """

    rows = session.execute(text(sql), params).mappings().all()

    buckets: List[Dict[str, Any]] = []
    total = 0

    for row in rows:
        count = int(row["bucket_count"])
        total += count
        buckets.append(
            {
                "verdict": str(row["verdict"]),
                "risk_tier": str(row["risk_tier"]),
                "count": count,
                "pct": 0.0,
            }
        )

    if total:
        for bucket in buckets:
            bucket["pct"] = round((bucket["count"] * 100.0) / total, 2)

    return {
        "total": total,
        "buckets": buckets,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_registry_verdict_tier_mix(
    session: Session = Depends(get_session),
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return get_verdict_tier_mix(session=session, source=source)


def compute_verdict_tier_mix(
    session: Session = Depends(get_session),
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return get_verdict_tier_mix(session=session, source=source)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite+pysqlite:///:memory:")
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    trust_score FLOAT,
                    last_assessed TIMESTAMP,
                    registry_source VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, verdict, risk_tier, trust_score, last_assessed, registry_source)
                VALUES
                    ('server-1', 'TRUSTED_GENERAL', 'LOW', 90.0, CURRENT_TIMESTAMP, 'seed'),
                    ('server-2', 'TRUSTED_GENERAL', 'LOW', 85.0, CURRENT_TIMESTAMP, 'seed'),
                    ('server-3', 'HIGH_RISK_ISOLATED', 'HIGH', 20.0, CURRENT_TIMESTAMP, 'seed')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine)
    with TestSession() as test_session:
        result = get_verdict_tier_mix(session=test_session)
        assert result["total"] == 3
        assert len(result["buckets"]) == 2
        assert any(
            bucket["verdict"] == "HIGH_RISK_ISOLATED"
            and bucket["risk_tier"] == "HIGH"
            and bucket["count"] == 1
            for bucket in result["buckets"]
        )

    print("PASS")