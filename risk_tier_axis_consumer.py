"""risk_tier_axis_consumer.py -- Scoring-consumer: reads mcp_llm_axis_scores per server,
aggregates the 7 axes into a risk_tier label, and returns the composite result.

Reads mcp_llm_axis_scores joined to mcp_server_registry for server metadata.
Applies trust_gating_override so official publishers are not shown as false HIGH/CRITICAL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

# Label -> numeric score (0-100 scale)
LABEL_SCORES: Dict[str, float] = {
    "CRITICAL": 95.0,
    "HIGH": 75.0,
    "MEDIUM": 50.0,
    "LOW": 25.0,
    "TRUSTED": 5.0,
    "UNKNOWN": 50.0,
    "NONE": 0.0,
}

# Axis -> weight (must sum to 1.0)
AXIS_WEIGHTS: Dict[str, float] = {
    "overall_risk": 0.20,
    "auth_strength": 0.10,
    "capability_breadth": 0.10,
    "data_sensitivity": 0.20,
    "network_egress": 0.10,
    "maintainer_trust": 0.15,
    "exploit_surface": 0.15,
}

RISK_TIER_THRESHOLDS = [
    (5.0, "TRUSTED_GENERAL"),
    (15.0, "TRUSTED_RESEARCH"),
    (35.0, "ENTERPRISE_CONTROLLED"),
    (55.0, "CAUTION_LIMITED"),
    (75.0, "HIGH_RISK_ISOLATED"),
    (90.0, "KNOWN_THREAT"),
]


def _label_score(label: Optional[str]) -> float:
    if not label:
        return 50.0
    return LABEL_SCORES.get(label.upper(), 50.0)


def _composite_score(labels: Dict[str, str]) -> float:
    total = 0.0
    for axis, weight in AXIS_WEIGHTS.items():
        total += _label_score(labels.get(axis)) * weight
    return round(total, 2)


def _derive_risk_tier(composite: float) -> str:
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if composite < threshold:
            return tier
    return "INSUFFICIENT"


def compute_risk_tier(server_id: str, db: Session) -> dict:
    """Read a server's 7 axis scores, compute composite + risk_tier, apply trust-gating."""
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    model_version = row[0] if row else None

    if model_version is None:
        return {
            "server_id": server_id,
            "risk_tier": "INSUFFICIENT",
            "composite_score": 0.0,
            "axis_breakdown": {},
            "scored_at": "",
        }

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == model_version,
        )
    ).scalars().all()

    labels: Dict[str, str] = {}
    axis_breakdown: Dict[str, Dict[str, object]] = {}
    for r in rows:
        if r.label:
            labels[r.axis_name] = r.label
        axis_breakdown[r.axis_name] = {
            "label": r.label,
            "p_top": r.p_top,
        }

    composite = _composite_score(labels)
    risk_tier = _derive_risk_tier(composite)

    # Apply trust gating for official publishers
    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None
    gate = trust_gate(url, name, labels)

    published_tier = gate.get("published_overall_risk")
    if published_tier:
        risk_tier = published_tier

    return {
        "server_id": server_id,
        "risk_tier": risk_tier,
        "composite_score": composite,
        "axis_breakdown": axis_breakdown,
        "scored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed: server1 = HIGH composite, server2 = MEDIUM composite, server3 = LOW composite
    s = TS()
    s.add(McpServerRegistry(server_id="srv_high", name="Malicious MCP",
                            url="https://example.com/malicious"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "CRITICAL"), ("auth_strength", "WEAK"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "UNKNOWN"),
                    ("exploit_surface", "CRITICAL")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv_high", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))

    for _i, (ax, lbl) in enumerate((("overall_risk", "MEDIUM"), ("auth_strength", "MODERATE"),
                    ("capability_breadth", "MODERATE"), ("data_sensitivity", "MODERATE"),
                    ("network_egress", "LIMITED"), ("maintainer_trust", "MODERATE"),
                    ("exploit_surface", "MODERATE")), start=10):
        s.add(McpLlmAxisScore(id=_i, server_id="srv_mid", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))

    for _i, (ax, lbl) in enumerate((("overall_risk", "TRUSTED"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "NARROW"), ("data_sensitivity", "LOW"),
                    ("network_egress", "NONE"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "LOW")), start=20):
        s.add(McpLlmAxisScore(id=_i, server_id="srv_low", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit(); s.close()

    # Test via db session directly (not HTTP)
    db = TS()
    try:
        result_high = compute_risk_tier("srv_high", db)
        result_mid = compute_risk_tier("srv_mid", db)
        result_low = compute_risk_tier("srv_low", db)
        result_none = compute_risk_tier("srv_nonexistent", db)
    finally:
        db.close()

    # Assert risk tiers differ
    assert result_high["risk_tier"] != result_mid["risk_tier"], \
        f"high={result_high['risk_tier']}, mid={result_mid['risk_tier']}"
    assert result_mid["risk_tier"] != result_low["risk_tier"], \
        f"mid={result_mid['risk_tier']}, low={result_low['risk_tier']}"
    assert result_high["risk_tier"] != result_low["risk_tier"], \
        f"high={result_high['risk_tier']}, low={result_low['risk_tier']}"

    # Assert composite scores in [0, 100]
    for r in [result_high, result_mid, result_low]:
        assert 0.0 <= r["composite_score"] <= 100.0, \
            f"{r['server_id']} composite={r['composite_score']} out of range"
        # Assert scored_at is ISO 8601
        assert r["scored_at"], "scored_at must not be empty"
        datetime.strptime(r["scored_at"], "%Y-%m-%dT%H:%M:%SZ")

    # Assert high > mid > low composite
    assert result_high["composite_score"] > result_mid["composite_score"], \
        f"high={result_high['composite_score']} not > mid={result_mid['composite_score']}"
    assert result_mid["composite_score"] > result_low["composite_score"], \
        f"mid={result_mid['composite_score']} not > low={result_low['composite_score']}"

    # Assert non-existent returns INSUFFICIENT
    assert result_none["risk_tier"] == "INSUFFICIENT", result_none

    print("PASS")
