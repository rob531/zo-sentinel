"""services/staged/registry_tier_mix_snapshot/logic.py

Data/computation layer for the `registry_tier_mix_snapshot` service.

Provides a single pure function that aggregates the server registry by
registry source and risk tier, handling NULL risk tiers as the "unscored"
bucket. The function works over a real SQLAlchemy Session obtained from the
application's DB layer.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry


def registry_tier_mix_snapshot(db: Session) -> Dict:
    """
    Return a snapshot of the registry tier mix.

    The result shape matches the contract expected by the router:

    {
        "generated_at": "<ISO8601 UTC timestamp>",
        "sources": [
            {
                "registry_source": "<source>",
                "total": <int>,
                "tiers": {"<tier>": <int>, ...}
            },
            ...
        ],
        "totals": {"<tier>": <int>, ...}
    }

    * Rows with a NULL ``risk_tier`` are counted under the tier name
      ``"unscored"``.
    * ``totals`` is the overall histogram across all sources.
    """
    # Query grouped by source and tier
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count(),
        ).group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    # Build per‑source aggregation
    source_map: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    overall: Dict[str, int] = defaultdict(int)

    for source, tier, count in rows:
        tier_name = tier or "unscored"
        source_map[source][tier_name] += int(count)
        overall[tier_name] += int(count)

    # Assemble the payload
    sources_list: List[Dict] = []
    for source, tiers_dict in source_map.items():
        total = sum(tiers_dict.values())
        sources_list.append(
            {
                "registry_source": source,
                "total": total,
                "tiers": dict(tiers_dict),
            }
        )

    payload = {
        "generated_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "sources": sources_list,
        "totals": dict(overall),
    }
    return payload


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine (mirrors the real DB schema)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Seed test data
    now = datetime.datetime.utcnow()
    test_rows = [
        McpServerRegistry(
            server_id="s1",
            registry_source="srcA",
            risk_tier="high",
            verdict="ok",
            trust_score=0.9,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="s2",
            registry_source="srcA",
            risk_tier=None,
            verdict="ok",
            trust_score=0.5,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="s3",
            registry_source="srcB",
            risk_tier="low",
            verdict="ok",
            trust_score=0.7,
            last_assessed=now,
        ),
    ]

    with SessionLocal() as sess:
        sess.add_all(test_rows)
        sess.commit()

        result = registry_tier_mix_snapshot(sess)

        # Assertions per contract
        assert result["sources"], "sources missing"
        assert len(result["sources"]) == 2, "expected 2 distinct sources"
        # Find the source that has the unscored tier
        unscored_found = any(
            "unscored" in src["tiers"] and src["tiers"]["unscored"] == 1
            for src in result["sources"]
        )
        assert unscored_found, "NULL risk_tier not counted as 'unscored'"

        total_counts = sum(result["totals"].values())
        assert total_counts == 3, f"totals sum {total_counts} != seeded rows 3"

        print("PASS")