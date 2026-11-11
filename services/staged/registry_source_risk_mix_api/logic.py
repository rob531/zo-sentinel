from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session, Base
from app.models import McpServerRegistry


def _aggregate(rows: List[McpServerRegistry]) -> Dict[str, Any]:
    """Aggregate a list of McpServerRegistry rows into the required response structure."""
    # risk tier categories (must match the spec exactly)
    tier_keys = [
        "HIGH_RISK_ISOLATED",
        "CAUTION_LIMITED",
        "ENTERPRISE_CONTROLLED",
        "TRUSTED_GENERAL",
        "INSUFFICIENT",
    ]

    source_data: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        src = row.registry_source
        if src not in source_data:
            source_data[src] = {
                "source": src,
                "total": 0,
                "tiers": {k: 0 for k in tier_keys},
                "trust_sum": 0.0,
                "trust_count": 0,
                "last_seen_max": None,  # type: Optional[datetime]
            }

        src_entry = source_data[src]
        src_entry["total"] += 1
        tier = row.risk_tier
        if tier in tier_keys:
            src_entry["tiers"][tier] += 1
        # accumulate trust score
        if row.trust_score is not None:
            src_entry["trust_sum"] += float(row.trust_score)
            src_entry["trust_count"] += 1
        # track latest last_seen
        if row.last_seen is not None:
            if (
                src_entry["last_seen_max"] is None
                or row.last_seen > src_entry["last_seen_max"]
            ):
                src_entry["last_seen_max"] = row.last_seen

    # Build final list of source dicts
    sources_list: List[Dict[str, Any]] = []
    for src_entry in source_data.values():
        mean_trust = (
            src_entry["trust_sum"] / src_entry["trust_count"]
            if src_entry["trust_count"]
            else 0.0
        )
        sources_list.append(
            {
                "source": src_entry["source"],
                "total": src_entry["total"],
                "tiers": src_entry["tiers"],
                "mean_trust_score": round(mean_trust, 6),
                "last_seen_max": (
                    src_entry["last_seen_max"].isoformat()
                    if src_entry["last_seen_max"]
                    else None
                ),
            }
        )

    # overall totals
    total_servers = sum(s["total"] for s in sources_list)
    total_sources = len(sources_list)

    return {"sources": sources_list, "totals": {"servers": total_servers, "sources": total_sources}}


def compute_source_risk_mix(
    source: Optional[str] = None, session: Session = Depends(get_session)
) -> Dict[str, Any]:
    """
    Summarise how MCP servers are distributed across risk tiers,
    optionally filtered by a specific registry_source.
    """
    query = session.query(McpServerRegistry)
    if source:
        query = query.filter(McpServerRegistry.registry_source == source)
    rows = query.all()
    return _aggregate(rows)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real schema
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed data: 4 servers, 2 sources, 3 risk tiers
    now = datetime.utcnow()
    seed = [
        McpServerRegistry(
            server_id="srv-1",
            registry_source="source-A",
            risk_tier="HIGH_RISK_ISOLATED",
            trust_score=0.5,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="srv-2",
            registry_source="source-A",
            risk_tier="CAUTION_LIMITED",
            trust_score=0.7,
            last_seen=now.replace(microsecond=0),
        ),
        McpServerRegistry(
            server_id="srv-3",
            registry_source="source-B",
            risk_tier="TRUSTED_GENERAL",
            trust_score=0.9,
            last_seen=now.replace(microsecond=0),
        ),
        McpServerRegistry(
            server_id="srv-4",
            registry_source="source-B",
            risk_tier="HIGH_RISK_ISOLATED",
            trust_score=0.4,
            last_seen=now,
        ),
    ]

    # Insert seed data
    db = TestSessionLocal()
    db.add_all(seed)
    db.commit()

    # Dependency override for the test
    def get_test_session() -> Session:  # pragma: no cover
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session

    # Direct call (bypassing FastAPI) using the test session
    result = compute_source_risk_mix(session=db)

    # ------------------------------------------------------------------- #
    # Assertions matching the contract
    # ------------------------------------------------------------------- #
    assert isinstance(result, dict)
    assert "sources" in result and "totals" in result
    assert len(result["sources"]) == 2, "expected 2 distinct sources"
    assert result["totals"]["servers"] == 4, "expected total of 4 servers"
    assert result["totals"]["sources"] == 2, "expected total of 2 sources"

    # Verify per‑source tier counts sum to the source total
    for src in result["sources"]:
        tier_sum = sum(src["tiers"].values())
        assert tier_sum == src["total"], f"tier counts mismatch for source {src['source']}"

    print("PASS")