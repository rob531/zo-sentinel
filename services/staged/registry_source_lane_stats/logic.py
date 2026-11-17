"""logic.py -- data/computation for the `registry_source_lane_stats` service.

Provides a single pure function that aggregates information from the
`mcp_server_registry` table, grouped by `registry_source`.  The router layer
injects a real SQLAlchemy Session via ``Depends(get_session)``; this module
contains no FastAPI code, only the computation.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def get_registry_source_lane_stats(db: Session) -> Mapping[str, Any]:
    """Aggregate per‑source statistics from ``McpServerRegistry``.

    Returns a mapping with two keys:
        * ``sources`` – a list of per‑source dictionaries containing:
            - ``registry_source`` (str)
            - ``server_count`` (int)
            - ``verdict_breakdown`` (dict[str, int])
            - ``risk_tier_breakdown`` (dict[str, int])
            - ``mean_trust_score`` (float | None)
            - ``mean_confidence`` (float | None)
            - ``never_scanned_count`` (int)
            - ``first_seen_at`` (datetime | None)
            - ``last_seen_at`` (datetime | None)
        * ``source_count`` – total number of distinct sources (int)
    """
    # Pull the columns we need; a full table scan is acceptable for the
    # modest size of the registry.
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.confidence,
            McpServerRegistry.last_scanned,
            McpServerRegistry.last_seen,
        )
    ).all()

    # Organise data per source.
    agg: dict[
        str,
        dict[
            str,
            Any,
        ],
    ] = {}
    for (
        source,
        verdict,
        risk_tier,
        trust_score,
        confidence,
        last_scanned,
        last_seen,
    ) in rows:
        if source is None:
            # Guard against null sources – they are ignored for the
            # purpose of this statistic.
            continue

        if source not in agg:
            agg[source] = {
                "server_count": 0,
                "verdict_counts": defaultdict(int),
                "risk_tier_counts": defaultdict(int),
                "trust_score_sum": 0.0,
                "trust_score_n": 0,
                "confidence_sum": 0.0,
                "confidence_n": 0,
                "never_scanned_count": 0,
                "first_seen_at": None,
                "last_seen_at": None,
            }

        src = agg[source]
        src["server_count"] += 1
        src["verdict_counts"][verdict or "unknown"] += 1
        src["risk_tier_counts"][risk_tier or "unknown"] += 1

        if trust_score is not None:
            src["trust_score_sum"] += float(trust_score)
            src["trust_score_n"] += 1

        if confidence is not None:
            src["confidence_sum"] += float(confidence)
            src["confidence_n"] += 1

        if not last_scanned:
            src["never_scanned_count"] += 1

        # Update min / max last_seen timestamps.
        if isinstance(last_seen, datetime):
            if src["first_seen_at"] is None or last_seen < src["first_seen_at"]:
                src["first_seen_at"] = last_seen
            if src["last_seen_at"] is None or last_seen > src["last_seen_at"]:
                src["last_seen_at"] = last_seen

    # Build the final payload.
    sources_list = []
    for source, data in agg.items():
        mean_trust = (
            data["trust_score_sum"] / data["trust_score_n"]
            if data["trust_score_n"]
            else None
        )
        mean_conf = (
            data["confidence_sum"] / data["confidence_n"]
            if data["confidence_n"]
            else None
        )
        sources_list.append(
            {
                "registry_source": source,
                "server_count": data["server_count"],
                "verdict_breakdown": dict(data["verdict_counts"]),
                "risk_tier_breakdown": dict(data["risk_tier_counts"]),
                "mean_trust_score": mean_trust,
                "mean_confidence": mean_conf,
                "never_scanned_count": data["never_scanned_count"],
                "first_seen_at": data["first_seen_at"],
                "last_seen_at": data["last_seen_at"],
            }
        )

    return {
        "sources": sources_list,
        "source_count": len(sources_list),
    }


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test builds an in‑memory SQLite DB, creates the required table,
    # seeds a few rows, runs the aggregation and checks the contract.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite engine.
    engine = create_engine("sqlite:///:memory:", future=True)

    # Create tables for the models we need.
    McpServerRegistry.__table__.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as sess:
        # Seed data: two distinct sources, three distinct verdicts.
        rows = [
            McpServerRegistry(
                server_id="srv-1",
                name="Server One",
                url="https://example.com/1",
                registry_source="source-A",
                verdict="verdict-1",
                risk_tier="high",
                trust_score=0.9,
                confidence=0.8,
                last_seen=datetime(2023, 1, 10, 12, 0, 0),
                last_scanned=None,
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="Server Two",
                url="https://example.com/2",
                registry_source="source-A",
                verdict="verdict-2",
                risk_tier="medium",
                trust_score=0.7,
                confidence=0.6,
                last_seen=datetime(2023, 1, 12, 15, 30, 0),
                last_scanned=datetime(2023, 1, 13, 9, 0, 0),
            ),
            McpServerRegistry(
                server_id="srv-3",
                name="Server Three",
                url="https://example.com/3",
                registry_source="source-B",
                verdict="verdict-3",
                risk_tier="low",
                trust_score=0.5,
                confidence=0.4,
                last_seen=datetime(2023, 2, 5, 8, 45, 0),
                last_scanned=None,
            ),
        ]
        sess.add_all(rows)
        sess.commit()

        result = get_registry_source_lane_stats(sess)

        # Contract checks.
        assert result["source_count"] == 2, "expected two distinct sources"
        # Find source-A entry.
        src_a = next(s for s in result["sources"] if s["registry_source"] == "source-A")
        assert src_a["verdict_breakdown"].get("verdict-1", 0) == 1
        assert src_a["verdict_breakdown"].get("verdict-2", 0) == 1
        # Ensure never_scanned_count reflects rows with null last_scanned.
        assert src_a["never_scanned_count"] == 1
        # Basic sanity on averages.
        assert round(src_a["mean_trust_score"], 2) == 0.80
        assert round(src_a["mean_confidence"], 2) == 0.70

        print("PASS")