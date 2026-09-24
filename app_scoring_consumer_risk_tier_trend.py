#!/usr/bin/env python
"""
Scoring consumer that computes risk‑tier transitions for MCP servers.

It reads :class:`app.models.McpLlmAxisScore` and :class:`app.models.McpServerRegistry`,
derives a simple risk tier from the scores, detects changes over time and writes the
results into a temporary ``risk_tier_trends`` table.

The module can be executed directly – it builds an in‑memory SQLite database,
seeds it with deterministic test data, runs the consumer and asserts the expected
output.
"""

from datetime import datetime
from typing import Iterable, List, Tuple

from sqlalchemy import Column, Integer, String, TIMESTAMP, text
from sqlalchemy.orm import Session, sessionmaker

# --------------------------------------------------------------------------- #
# App imports – must be used for all production data access
# --------------------------------------------------------------------------- #
from app.db import get_session, Base
from app.models import McpLlmAxisScore, McpServerRegistry

# --------------------------------------------------------------------------- #
# Helper – risk tier derivation from a single score
# --------------------------------------------------------------------------- #
def _tier_from_score(score: McpLlmAxisScore) -> str:
    """Very small heuristic used only for the self‑test.

    - ``p_critical`` ≥ 0.5 → ``'critical'``
    - otherwise ``p_danger`` ≥ 0.5 → ``'danger'``
    - else ``'low'``
    """
    if score.p_critical is not None and score.p_critical >= 0.5:
        return "critical"
    if score.p_danger is not None and score.p_danger >= 0.5:
        return "danger"
    return "low"


# --------------------------------------------------------------------------- #
# Core consumer
# --------------------------------------------------------------------------- #
def compute_risk_tier_trends(session: Session) -> None:
    """Populate the ``risk_tier_trends`` table.

    The table is created on‑the‑fly if it does not exist.  Each row records a
    transition for a server from one tier to another together with the timestamp
    of the change.
    """
    # ------------------------------------------------------------------- #
    # Ensure the destination table exists
    # ------------------------------------------------------------------- #
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS risk_tier_trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                from_tier TEXT,
                to_tier TEXT,
                changed_at TIMESTAMP NOT NULL
            )
            """
        )
    )
    session.commit()

    # ------------------------------------------------------------------- #
    # Gather scores ordered by server and time
    # ------------------------------------------------------------------- #
    scores: List[McpLlmAxisScore] = (
        session.query(McpLlmAxisScore)
        .order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.scored_at)
        .all()
    )

    # Group scores per server
    grouped: dict[str, List[McpLlmAxisScore]] = {}
    for s in scores:
        grouped.setdefault(s.server_id, []).append(s)

    # ------------------------------------------------------------------- #
    # Detect transitions and insert them
    # ------------------------------------------------------------------- #
    for server_id, srv_scores in grouped.items():
        previous_tier: str | None = None
        for score in srv_scores:
            current_tier = _tier_from_score(score)
            if previous_tier is not None and current_tier != previous_tier:
                session.execute(
                    text(
                        """
                        INSERT INTO risk_tier_trends
                        (server_id, from_tier, to_tier, changed_at)
                        VALUES (:server_id, :from_tier, :to_tier, :changed_at)
                        """
                    ),
                    {
                        "server_id": server_id,
                        "from_tier": previous_tier,
                        "to_tier": current_tier,
                        "changed_at": score.scored_at,
                    },
                )
            previous_tier = current_tier

    session.commit()


# --------------------------------------------------------------------------- #
# Self‑test when run as a script
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite engine and override the app's session factory
    # ------------------------------------------------------------------- #
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)

    # Override the dependency used by the production code
    def _test_session() -> Session:  # pragma: no cover
        return sessionmaker(bind=engine)()

    # Replace the imported get_session with our test version
    # (the consumer imports it at module load time, so we monkey‑patch the name)
    import sys

    current_module = sys.modules[__name__]
    setattr(current_module, "get_session", _test_session)

    # ------------------------------------------------------------------- #
    # Seed deterministic data
    # ------------------------------------------------------------------- #
    sess = _test_session()

    # Servers
    srv1 = McpServerRegistry(
        server_id="srv-1",
        name="Server One",
        risk_tier="low",
        confidence=1.0,
        description="",
        first_seen=datetime(2023, 1, 1),
        last_seen=datetime(2023, 1, 3),
        last_scanned=datetime(2023, 1, 3),
        last_assessed=datetime(2023, 1, 3),
        meta="{}",
        registry_source="test",
        scan_count=1,
        trust_score=1.0,
        url="http://example.com",
        verdict="",
        verdict_reasoning="",
    )
    srv2 = McpServerRegistry(
        server_id="srv-2",
        name="Server Two",
        risk_tier="low",
        confidence=1.0,
        description="",
        first_seen=datetime(2023, 1, 1),
        last_seen=datetime(2023, 1, 2),
        last_scanned=datetime(2023, 1, 2),
        last_assessed=datetime(2023, 1, 2),
        meta="{}",
        registry_source="test",
        scan_count=1,
        trust_score=1.0,
        url="http://example.org",
        verdict="",
        verdict_reasoning="",
    )
    sess.add_all([srv1, srv2])

    # Scores – note the unique (server_id, axis_name, model_version) constraint.
    # We vary ``axis_name`` to keep the combination unique.
    scores = [
        McpLlmAxisScore(
            id="s1-1",
            server_id="srv-1",
            axis_name="test-a",
            label="",
            label_index=0,
            probs="{}",
            p_top=0.7,
            p_critical=0.1,
            p_danger=0.2,
            escalated=0,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="",
            scored_at=datetime(2023, 1, 1, 0, 0, 0),
        ),
        McpLlmAxisScore(
            id="s1-2",
            server_id="srv-1",
            axis_name="test-b",
            label="",
            label_index=0,
            probs="{}",
            p_top=0.1,
            p_critical=0.6,
            p_danger=0.3,
            escalated=0,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="",
            scored_at=datetime(2023, 1, 2, 0, 0, 0),
        ),
        McpLlmAxisScore(
            id="s1-3",
            server_id="srv-1",
            axis_name="test-c",
            label="",
            label_index=0,
            probs="{}",
            p_top=0.05,
            p_critical=0.1,
            p_danger=0.85,
            escalated=0,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="",
            scored_at=datetime(2023, 1, 3, 0, 0, 0),
        ),
        McpLlmAxisScore(
            id="s2-1",
            server_id="srv-2",
            axis_name="test-a",
            label="",
            label_index=0,
            probs="{}",
            p_top=0.9,
            p_critical=0.0,
            p_danger=0.1,
            escalated=0,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="",
            scored_at=datetime(2023, 1, 1, 0, 0, 0),
        ),
        McpLlmAxisScore(
            id="s2-2",
            server_id="srv-2",
            axis_name="test-b",
            label="",
            label_index=0,
            probs="{}",
            p_top=0.8,
            p_critical=0.0,
            p_danger=0.6,
            escalated=0,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="",
            scored_at=datetime(2023, 1, 2, 0, 0, 0),
        ),
    ]
    sess.add_all(scores)
    sess.commit()

    # ------------------------------------------------------------------- #
    # Run the consumer
    # ------------------------------------------------------------------- #
    compute_risk_tier_trends(sess)

    # ------------------------------------------------------------------- #
    # Verify results
    # ------------------------------------------------------------------- #
    rows = sess.execute(text("SELECT server_id, from_tier, to_tier, changed_at FROM risk_tier_trends ORDER BY server_id, changed_at")).fetchall()

    # Expected transitions:
    # srv-1: low -> critical (day 2), critical -> danger (day 3)
    # srv-2: low -> danger (day 2)  (no change on day 1)
    expected: List[Tuple[str, str | None, str, datetime]] = [
        ("srv-1", "low", "critical", datetime(2023, 1, 2, 0, 0, 0)),
        ("srv-1", "critical", "danger", datetime(2023, 1, 3, 0, 0, 0)),
        ("srv-2", "low", "danger", datetime(2023, 1, 2, 0, 0, 0)),
    ]

    assert len(rows) == len(expected), f"expected {len(expected)} rows, got {len(rows)}"
    for row, exp in zip(rows, expected):
        assert row.server_id == exp[0]
        assert row.from_tier == exp[1]
        assert row.to_tier == exp[2]
        # SQLite returns strings for timestamps; compare ISO format
        assert datetime.fromisoformat(row.changed_at) == exp[3]

    print("PASS")