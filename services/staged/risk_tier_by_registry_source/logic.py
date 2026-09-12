"""
Risk tier distribution by registry source.
Groups McpServerRegistry by registry_source and risk_tier,
using overall_risk scores from mcp_llm_axis_scores.
"""

from typing import List, Optional
from collections import defaultdict

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


class TierCount(BaseModel):
    tier: str
    count: int


class SourceBreakdown(BaseModel):
    source: str
    tiers: List[TierCount]


class RiskTierBySourceResponse(BaseModel):
    sources: List[SourceBreakdown]


def get_risk_tier_by_source(session: Session) -> RiskTierBySourceResponse:
    """
    Join mcp_server_registry with mcp_llm_axis_scores (axis_name='overall_risk'),
    group by registry_source and risk_tier, return aggregated counts.
    """
    result = session.execute(
        text("""
            SELECT
                sr.registry_source,
                sr.risk_tier,
                COUNT(*) as server_count
            FROM mcp_server_registry sr
            INNER JOIN mcp_llm_axis_scores ax
                ON ax.server_id = sr.server_id
                AND ax.axis_name = 'overall_risk'
            WHERE sr.registry_source IS NOT NULL
                AND sr.risk_tier IS NOT NULL
            GROUP BY sr.registry_source, sr.risk_tier
            ORDER BY sr.registry_source, sr.risk_tier
        """)
    )
    rows = result.fetchall()

    grouped: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for registry_source, risk_tier, server_count in rows:
        grouped[registry_source][risk_tier] = server_count

    sources = []
    for source in sorted(grouped.keys()):
        tiers_data = grouped[source]
        tiers = [TierCount(tier=tier, count=tiers_data[tier]) for tier in sorted(tiers_data.keys())]
        sources.append(SourceBreakdown(source=source, tiers=tiers))

    return RiskTierBySourceResponse(sources=sources)


def get_registry_source_tier_map(session: Session) -> dict[str, dict[str, int]]:
    """
    Returns a simple dict mapping registry_source -> {risk_tier: count}.
    Used by other services that need this aggregated view.
    """
    response = get_risk_tier_by_source(session)
    return {s.source: {t.tier: t.count for t in s.tiers} for s in response.sources}


def compare_tiers(source_a: str, source_b: str, session: Session) -> dict:
    """
    Compare risk tier distribution between two registry sources.
    Returns diff summary used by org_risk_tier_compare service.
    """
    tier_map = get_registry_source_tier_map(session)
    tiers_a = tier_map.get(source_a, {})
    tiers_b = tier_map.get(source_b, {})

    all_tiers = set(tiers_a.keys()) | set(tiers_b.keys())
    diff = {}
    for tier in all_tiers:
        count_a = tiers_a.get(tier, 0)
        count_b = tiers_b.get(tier, 0)
        diff[tier] = {"source_a": count_a, "source_b": count_b, "delta": count_a - count_b}

    return diff


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory self-test with seeded data
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id INTEGER PRIMARY KEY,
                registry_source TEXT,
                risk_tier TEXT,
                name TEXT,
                url TEXT,
                description TEXT,
                trust_score REAL,
                confidence REAL,
                scan_count INTEGER,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                meta TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                probs TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                model_version TEXT,
                decision_rule_version TEXT,
                scored_at TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                adapter_sha256 TEXT
            )
        """))
        conn.commit()

    # Seed 3 servers across 2 sources with 2 distinct tiers each
    seed_data = [
        # Source A servers
        (1, "SourceA", "critical"),
        (2, "SourceA", "high"),
        # Source B servers
        (3, "SourceB", "critical"),
        (4, "SourceB", "medium"),
        (5, "SourceB", "low"),
    ]

    with engine.connect() as conn:
        for server_id, source, tier in seed_data:
            conn.execute(
                text("INSERT INTO mcp_server_registry VALUES (:sid, :src, :tier, NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"),
                {"sid": server_id, "src": source, "tier": tier}
            )
            conn.execute(
                text("INSERT INTO mcp_llm_axis_scores VALUES (NULL, :sid, 'overall_risk', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL)"),
                {"sid": server_id}
            )
        conn.commit()

    TestingSessionLocal = sessionmaker(bind=engine)
    test_session = TestingSessionLocal()

    # Override the session dependency
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: test_session

    # Run the function under test
    response = get_risk_tier_by_source(test_session)

    # Verify structure
    assert isinstance(response, RiskTierBySourceResponse), f"Expected RiskTierBySourceResponse, got {type(response)}"
    assert len(response.sources) == 2, f"Expected 2 sources, got {len(response.sources)}"

    sources_by_name = {s.source: s for s in response.sources}

    # SourceA: critical=1, high=1
    assert "SourceA" in sources_by_name, f"SourceA not found in {list(sources_by_name.keys())}"
    source_a_tiers = {t.tier: t.count for t in sources_by_name["SourceA"].tiers}
    assert source_a_tiers.get("critical") == 1, f"SourceA critical expected 1, got {source_a_tiers.get('critical')}"
    assert source_a_tiers.get("high") == 1, f"SourceA high expected 1, got {source_a_tiers.get('high')}"
    assert len(source_a_tiers) == 2, f"SourceA should have 2 tiers, got {len(source_a_tiers)}"

    # SourceB: critical=1, medium=1, low=1
    assert "SourceB" in sources_by_name, f"SourceB not found in {list(sources_by_name.keys())}"
    source_b_tiers = {t.tier: t.count for t in sources_by_name["SourceB"].tiers}
    assert source_b_tiers.get("critical") == 1, f"SourceB critical expected 1, got {source_b_tiers.get('critical')}"
    assert source_b_tiers.get("medium") == 1, f"SourceB medium expected 1, got {source_b_tiers.get('medium')}"
    assert source_b_tiers.get("low") == 1, f"SourceB low expected 1, got {source_b_tiers.get('low')}"
    assert len(source_b_tiers) == 3, f"SourceB should have 3 tiers, got {len(source_b_tiers)}"

    test_session.close()
    print("PASS")