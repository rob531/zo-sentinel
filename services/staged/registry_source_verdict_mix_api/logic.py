from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry


def get_verdict_mix(session=Depends(get_session)) -> Dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT
                registry_source,
                risk_tier,
                COUNT(*) AS tier_count,
                SUM(trust_score) AS trust_sum,
                COUNT(trust_score) AS trust_count,
                SUM(confidence) AS confidence_sum,
                COUNT(confidence) AS confidence_count
            FROM mcp_server_registry
            GROUP BY registry_source, risk_tier
            ORDER BY registry_source, risk_tier
            """
        )
    ).mappings().all()

    sources_by_name: Dict[Any, Dict[str, Any]] = {}

    for row in rows:
        registry_source = row["registry_source"]
        source = sources_by_name.setdefault(
            registry_source,
            {
                "registry_source": registry_source,
                "total_servers": 0,
                "avg_trust_score": None,
                "avg_confidence": None,
                "tier_counts": {},
                "_trust_sum": 0.0,
                "_trust_count": 0,
                "_confidence_sum": 0.0,
                "_confidence_count": 0,
            },
        )

        tier_count = int(row["tier_count"])
        risk_tier = row["risk_tier"]
        source["total_servers"] += tier_count
        source["tier_counts"][risk_tier] = tier_count

        if row["trust_sum"] is not None:
            source["_trust_sum"] += float(row["trust_sum"])
            source["_trust_count"] += int(row["trust_count"])

        if row["confidence_sum"] is not None:
            source["_confidence_sum"] += float(row["confidence_sum"])
            source["_confidence_count"] += int(row["confidence_count"])

    sources: List[Dict[str, Any]] = []
    for source in sources_by_name.values():
        if source["_trust_count"]:
            source["avg_trust_score"] = (
                source["_trust_sum"] / source["_trust_count"]
            )
        if source["_confidence_count"]:
            source["avg_confidence"] = (
                source["_confidence_sum"] / source["_confidence_count"]
            )

        source.pop("_trust_sum", None)
        source.pop("_trust_count", None)
        source.pop("_confidence_sum", None)
        source.pop("_confidence_count", None)
        sources.append(source)

    return {
        "sources": sources,
        "generated_at": datetime.now(timezone.utc),
    }


def get_registry_source_verdict_mix(session=Depends(get_session)) -> Dict[str, Any]:
    return get_verdict_mix(session)


get_source_verdict_mix = get_verdict_mix


if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    confidence REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, risk_tier, trust_score, confidence)
                VALUES
                    ('s1', 'Server 1', 'registry-a', 'critical', 0.90, 0.80),
                    ('s2', 'Server 2', 'registry-a', 'low', 0.50, 0.70),
                    ('s3', 'Server 3', 'registry-a', 'medium', 0.70, 0.90),
                    ('s4', 'Server 4', 'registry-b', 'critical', 0.30, 0.40),
                    ('s5', 'Server 5', 'registry-b', 'medium', 0.60, 0.60)
                """
            )
        )

    def override_get_session():
        with test_engine.connect() as connection:
            yield connection

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/sources/verdict-mix")
    def verdict_mix_endpoint(session=Depends(get_session)):
        return get_verdict_mix(session)

    with TestClient(test_app) as client:
        response = client.get("/api/registry/sources/verdict-mix")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["sources"]) == 2

        by_source = {
            source["registry_source"]: source for source in payload["sources"]
        }
        expected_trust = {
            "registry-a": 0.70,
            "registry-b": 0.45,
        }
        expected_confidence = {
            "registry-a": 0.80,
            "registry-b": 0.50,
        }

        for source_name, source in by_source.items():
            assert sum(source["tier_counts"].values()) == source["total_servers"]
            assert abs(source["avg_trust_score"] - expected_trust[source_name]) <= 0.01
            assert (
                abs(source["avg_confidence"] - expected_confidence[source_name])
                <= 0.01
            )

    print("PASS")