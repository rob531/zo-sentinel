"""Logic for the server_family_aggregation service.

Provides aggregation of servers from the `mcp_server_registry` table,
grouped by `registry_source` and by the first token of the server name
(case‑insensitive).

The public entry point is :func:`server_family_aggregation` which accepts a
SQLAlchemy session and returns a JSON‑serialisable mapping compatible with
the router contract.
"""

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any

from sqlalchemy.orm import Session
from fastapi import Depends

from app.db import get_session, Base, SessionLocal
from app.models import McpServerRegistry


def _group_key(row: McpServerRegistry) -> tuple:
    """Return the grouping key (registry_source, name_prefix)."""
    name_prefix = (
        row.name.split()[0].lower()
        if isinstance(row.name, str) and row.name.strip()
        else ""
    )
    return (row.registry_source, name_prefix)


def server_family_aggregation(session: Session) -> Dict[str, List[Dict[str, Any]]]:
    """
    Compute the aggregation.

    Returns
    -------
    dict
        ``{"groups": [ ... ]}`` where each element contains:

        - registry_source (str)
        - name_prefix (str)
        - server_count (int)
        - avg_trust_score (float | None)
        - tiers (dict mapping risk_tier -> count)
        - first_seen (datetime | None)
        - last_seen (datetime | None)
    """
    rows = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
        )
        .all()
    )

    # structure: {(registry_source, name_prefix): aggregation dict}
    agg: Dict[tuple, Dict[str, Any]] = defaultdict(
        lambda: {
            "server_count": 0,
            "trust_score_sum": 0.0,
            "tiers": defaultdict(int),
            "first_seen": None,
            "last_seen": None,
        }
    )

    for (
        _sid,
        name,
        registry_source,
        risk_tier,
        trust_score,
        first_seen,
        last_seen,
    ) in rows:
        key = (registry_source, name.split()[0].lower() if name else "")
        data = agg[key]

        data["server_count"] += 1
        data["trust_score_sum"] += trust_score or 0.0
        data["tiers"][risk_tier] += 1

        if first_seen:
            if data["first_seen"] is None or first_seen < data["first_seen"]:
                data["first_seen"] = first_seen
        if last_seen:
            if data["last_seen"] is None or last_seen > data["last_seen"]:
                data["last_seen"] = last_seen

    groups = []
    for (registry_source, name_prefix), data in agg.items():
        avg = (
            data["trust_score_sum"] / data["server_count"]
            if data["server_count"]
            else None
        )
        groups.append(
            {
                "registry_source": registry_source,
                "name_prefix": name_prefix,
                "server_count": data["server_count"],
                "avg_trust_score": avg,
                "tiers": dict(data["tiers"]),
                "first_seen": data["first_seen"],
                "last_seen": data["last_seen"],
            }
        )

    return {"groups": groups}


# FastAPI dependency wrapper (used by the router)
def get_server_family_summary(
    session: Session = Depends(get_session),
) -> Dict[str, List[Dict[str, Any]]]:
    """Dependency that returns the aggregation for the current request."""
    return server_family_aggregation(session)


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # Build an in‑memory SQLite DB using the real models.
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestSession = SessionLocal(bind=engine)

    # Seed data: 6 servers, 2 registry sources, 3 name prefixes.
    now = datetime.utcnow()
    seed = [
        McpServerRegistry(
            server_id="s1",
            name="alpha‑one",
            registry_source="sourceA",
            risk_tier="high",
            trust_score=0.9,
            first_seen=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="s2",
            name="alpha‑two",
            registry_source="sourceA",
            risk_tier="medium",
            trust_score=0.8,
            first_seen=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="s3",
            name="beta‑one",
            registry_source="sourceA",
            risk_tier="low",
            trust_score=0.7,
            first_seen=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="s4",
            name="beta‑two",
            registry_source="sourceB",
            risk_tier="high",
            trust_score=0.6,
            first_seen=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="s5",
            name="gamma‑one",
            registry_source="sourceB",
            risk_tier="medium",
            trust_score=0.5,
            first_seen=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="s6",
            name="gamma‑two",
            registry_source="sourceB",
            risk_tier="low",
            trust_score=0.4,
            first_seen=now,
            last_seen=now,
        ),
    ]

    TestSession.add_all(seed)
    TestSession.commit()

    # Run the aggregation.
    result = server_family_aggregation(TestSession)

    # Basic contract assertions.
    assert isinstance(result, dict), "Result must be a dict"
    groups = result.get("groups", [])
    assert len(groups) >= 2, "At least two groups expected"
    # Ensure at least one registry_source has >=2 servers across its groups.
    source_counts: Dict[str, int] = defaultdict(int)
    for g in groups:
        source_counts[g["registry_source"]] += g["server_count"]
    assert any(cnt >= 2 for cnt in source_counts.values()), "One source should have >=2 servers"

    print("PASS")