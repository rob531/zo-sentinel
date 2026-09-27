# services/staged/cascade_family_rollup/logic.py
from __future__ import annotations

import datetime
import urllib.parse
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class FamilySeriesItem(BaseModel):
    family: str = Field(..., description="Top‑level domain / host extracted from server URL")
    server_count: int = Field(..., description="Number of servers in this family")
    avg_trust_score: float = Field(..., description="Average trust_score of the servers")
    risk_tiers: Dict[str, int] = Field(
        ..., description="Mapping of risk_tier value to number of servers"
    )
    last_seen: Optional[datetime.datetime] = Field(
        None, description="Most recent last_seen timestamp among the servers"
    )
    first_seen: Optional[datetime.datetime] = Field(
        None, description="Earliest first_seen timestamp among the servers"
    )


class FamilyRollupResponse(BaseModel):
    total_families: int = Field(..., description="Number of families returned")
    total_servers: int = Field(..., description="Total number of servers across all families")
    series: List[FamilySeriesItem] = Field(..., description="Per‑family aggregates")


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _extract_family(url: str) -> str:
    """
    Extract the host part of a URL. If the URL lacks a scheme, ``urlparse`` will
    treat the whole string as a path – in that case we fall back to a simple
    split on '/' and take the first segment.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc or parsed.path.split("/")[0]
    return host.lower()


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def get_family_rollup(
    min_servers: int = 1,
    db: Session = Depends(get_session),
) -> FamilyRollupResponse:
    """
    Compute per‑family aggregates for servers that have at least one associated
    LLM axis score.

    Parameters
    ----------
    min_servers: int
        Minimum number of servers a family must contain to be included.
    db: Session
        SQLAlchemy session (injected by FastAPI).

    Returns
    -------
    FamilyRollupResponse
        Structured response containing aggregates.
    """
    # Join to ensure we only consider servers that have at least one score.
    server_rows = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.url,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_seen,
            McpServerRegistry.first_seen,
        )
        .join(McpLlmAxisScore, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .distinct()
        .all()
    )

    families: Dict[str, List[McpServerRegistry]] = defaultdict(list)

    for row in server_rows:
        family = _extract_family(row.url or "")
        families[family].append(row)

    # Apply min_servers filter and build series items
    series_items: List[FamilySeriesItem] = []
    total_servers = 0

    for family, rows in families.items():
        if len(rows) < min_servers:
            continue

        server_count = len(rows)
        total_servers += server_count
        avg_trust = (
            sum(r.trust_score for r in rows if r.trust_score is not None) / server_count
        )
        risk_dist: Dict[str, int] = defaultdict(int)
        last_seen_vals = []
        first_seen_vals = []

        for r in rows:
            tier = r.risk_tier or "unknown"
            risk_dist[tier] += 1
            if r.last_seen:
                last_seen_vals.append(r.last_seen)
            if r.first_seen:
                first_seen_vals.append(r.first_seen)

        series_items.append(
            FamilySeriesItem(
                family=family,
                server_count=server_count,
                avg_trust_score=round(avg_trust, 2),
                risk_tiers=dict(risk_dist),
                last_seen=max(last_seen_vals) if last_seen_vals else None,
                first_seen=min(first_seen_vals) if first_seen_vals else None,
            )
        )

    response = FamilyRollupResponse(
        total_families=len(series_items),
        total_servers=total_servers,
        series=sorted(series_items, key=lambda x: x.family),
    )
    return response


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real schema
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_db: Session = TestSession()

    # ------------------------------------------------------------------- #
    # Seed data: 4 servers across 2 families, each with a single score row
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    servers = [
        McpServerRegistry(
            server_id=1,
            url="https://github.com/repo1",
            trust_score=80.0,
            risk_tier="low",
            first_seen=now - datetime.timedelta(days=10),
            last_seen=now - datetime.timedelta(days=1),
        ),
        McpServerRegistry(
            server_id=2,
            url="https://github.com/repo2",
            trust_score=70.0,
            risk_tier="medium",
            first_seen=now - datetime.timedelta(days=9),
            last_seen=now - datetime.timedelta(days=2),
        ),
        McpServerRegistry(
            server_id=3,
            url="https://api.example.com/service1",
            trust_score=60.0,
            risk_tier="high",
            first_seen=now - datetime.timedelta(days=8),
            last_seen=now - datetime.timedelta(days=3),
        ),
        McpServerRegistry(
            server_id=4,
            url="https://api.example.com/service2",
            trust_score=90.0,
            risk_tier="low",
            first_seen=now - datetime.timedelta(days=7),
            last_seen=now - datetime.timedelta(days=4),
        ),
    ]

    scores = [
        McpLlmAxisScore(
            id=1,
            server_id=1,
            adapter_sha256="a" * 64,
            axis_name="test_axis",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            label="label",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.7,
            probs="{}",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2,
            server_id=2,
            adapter_sha256="b" * 64,
            axis_name="test_axis",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            label="label",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.7,
            probs="{}",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=3,
            server_id=3,
            adapter_sha256="c" * 64,
            axis_name="test_axis",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            label="label",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.7,
            probs="{}",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=4,
            server_id=4,
            adapter_sha256="d" * 64,
            axis_name="test_axis",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            label="label",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.7,
            probs="{}",
            scored_at=now,
        ),
    ]

    test_db.add_all(servers)
    test_db.add_all(scores)
    test_db.commit()

    # ------------------------------------------------------------------- #
    # Execute the core logic against the test DB
    # ------------------------------------------------------------------- #
    result = get_family_rollup(min_servers=1, db=test_db)

    # ------------------------------------------------------------------- #
    # Assertions as per the acceptance criteria
    # ------------------------------------------------------------------- #
    assert result.total_families == 2, f"expected 2 families, got {result.total_families}"
    assert result.total_servers == 4, f"expected 4 servers, got {result.total_servers}"
    assert len(result.series) == 2, f"expected series length 2, got {len(result.series)}"
    summed = sum(item.server_count for item in result.series)
    assert summed == 4, f"expected sum of server_counts 4, got {summed}"

    print("PASS")