"""
services/staged/ladder_rung_convergence_report/logic.py
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, stdev
from typing import Dict

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real application data layer imports
from app.db import get_session, Base
from app.models import McpLlmAxisScore, McpServerRegistry


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class AxisStats(BaseModel):
    mean: float
    stddev: float


class ConvergenceReport(BaseModel):
    wave: int
    axes: Dict[str, AxisStats]


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def get_convergence_report(
    wave: int,
    session: Session = Depends(get_session),
) -> ConvergenceReport:
    """
    Compute per‑axis mean and population standard deviation of the ``p_top``
    scores for a given scoring wave.

    The ``wave`` identifier is stored in the ``label_index`` column of
    :class:`McpLlmAxisScore`.  Only rows matching the supplied wave are
    considered.

    Returns a :class:`ConvergenceReport` instance.
    """
    # Pull the raw scores for the requested wave
    stmt = (
        select(McpLlmAxisScore.axis_name, McpLlmAxisScore.p_top)
        .where(McpLlmAxisScore.label_index == wave)
    )
    rows = session.execute(stmt).all()

    # Group scores by axis
    axis_groups: Dict[str, list[float]] = defaultdict(list)
    for axis_name, p_top in rows:
        if p_top is not None:
            axis_groups[axis_name].append(p_top)

    # Compute statistics
    axes_stats: Dict[str, AxisStats] = {}
    for axis_name, values in axis_groups.items():
        if len(values) < 2:
            # stdev requires at least two data points; fall back to 0.0
            std = 0.0
        else:
            std = stdev(values)
        axes_stats[axis_name] = AxisStats(mean=mean(values), stddev=std)

    return ConvergenceReport(wave=wave, axes=axes_stats)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import random
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup – overrides the real DB dependency
    # ------------------------------------------------------------------- #
    TEST_ENGINE = create_engine("sqlite:///:memory:", echo=False)
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE)

    # Create tables based on the real model metadata
    Base.metadata.create_all(TEST_ENGINE)

    # Dependency override for the test
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data: 3 waves, 2 servers each, 2 axes with varying scores
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        # Servers
        servers = [
            McpServerRegistry(
                server_id=f"svr_{wave}_{i}",
                name=f"Server {wave}-{i}",
                confidence=0.9,
                description="test server",
                first_seen=None,
                last_assessed=None,
                last_scanned=None,
                last_seen=None,
                meta={},
                registry_source="test",
                risk_tier="low",
                scan_count=0,
                trust_score=1.0,
                url="http://example.com",
                verdict="clean",
                verdict_reasoning="",
            )
            for wave in (1, 2, 3)
            for i in (1, 2)
        ]
        db.add_all(servers)

        # Axis scores
        axes = ["axis_alpha", "axis_beta"]
        for wave in (1, 2, 3):
            for srv in servers:
                if not srv.server_id.startswith(f"svr_{wave}_"):
                    continue
                for axis in axes:
                    db.add(
                        McpLlmAxisScore(
                            adapter_sha256="dummy",
                            axis_name=axis,
                            decision_rule_version="v1",
                            escalated=False,
                            escalated_to=None,
                            id=None,
                            label=f"wave{wave}",
                            label_index=wave,  # <-- wave identifier
                            model_version="m1",
                            p_critical=random.random(),
                            p_danger=random.random(),
                            p_top=random.random(),
                            probs={},
                            scored_at=None,
                            server_id=srv.server_id,
                        )
                    )
        db.commit()

        # ---------------------------------------------------------------- #
        # Execute the core logic for wave 1 and validate the result
        # ---------------------------------------------------------------- #
        report = get_convergence_report(wave=1, session=db)

        # At least one axis must have a non‑zero stddev (scores are random)
        assert any(stat.stddev > 0 for stat in report.axes.values()), "stddev should be > 0"
        print("PASS")