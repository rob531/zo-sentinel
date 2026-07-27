# services/staged/risk_summary/contract.py
from collections import Counter
from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

# Real data layer imports – must remain unchanged for production
from app.db import get_session, Base
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


class AxisAverage(BaseModel):
    avg_p_top: float = Field(..., alias="avg_p_top")
    avg_p_critical: float = Field(..., alias="avg_p_critical")
    avg_p_danger: float = Field(..., alias="avg_p_danger")


class RiskSummaryResponse(BaseModel):
    tiers: Dict[str, int]
    axis_averages: Dict[str, AxisAverage]
    total_servers: int
    assessed_servers: int


@router.get("/risk/summary", response_model=RiskSummaryResponse)
def get_risk_summary(session: Session = Depends(get_session)):
    # Total number of servers (regardless of verdict)
    total_servers = session.query(func.count()).select_from(McpServerRegistry).scalar()

    # Servers that have a current verdict
    assessed_q = session.query(McpServerRegistry).filter(McpServerRegistry.verdict.isnot(None))
    assessed_servers = assessed_q.count()
    assessed = assessed_q.all()

    # Tier counts
    tier_counts = Counter(s.risk_tier for s in assessed)

    # Axis averages across assessed servers
    axis_q = (
        session.query(
            McpLlmAxisScore.axis,
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .filter(McpServerRegistry.verdict.isnot(None))
        .group_by(McpLlmAxisScore.axis)
    )
    axis_averages: Dict[str, AxisAverage] = {}
    for row in axis_q:
        axis_averages[row.axis] = AxisAverage(
            avg_p_top=row.avg_p_top,
            avg_p_critical=row.avg_p_critical,
            avg_p_danger=row.avg_p_danger,
        )

    return RiskSummaryResponse(
        tiers=dict(tier_counts),
        axis_averages=axis_averages,
        total_servers=total_servers,
        assessed_servers=assessed_servers,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.risk_summary.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite engine that mimics the real DB schema
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    # Dependency override to use the SQLite session
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    # Seed data
    with TestSessionLocal() as db:
        # Servers (5 total, 4 assessed)
        servers = [
            McpServerRegistry(server_id="s1", risk_tier="low", verdict="ok"),
            McpServerRegistry(server_id="s2", risk_tier="low", verdict="ok"),
            McpServerRegistry(server_id="s3", risk_tier="medium", verdict="ok"),
            McpServerRegistry(server_id="s4", risk_tier="high", verdict="ok"),
            McpServerRegistry(server_id="s5", risk_tier="high", verdict=None),
        ]
        db.add_all(servers)

        # Axis scores for the 4 assessed servers
        axis_data = [
            # server s1
            McpLlmAxisScore(
                server_id="s1",
                axis="confidentiality",
                p_top=0.9,
                p_critical=0.5,
                p_danger=0.2,
            ),
            McpLlmAxisScore(
                server_id="s1",
                axis="integrity",
                p_top=0.8,
                p_critical=0.4,
                p_danger=0.1,
            ),
            # server s2
            McpLlmAxisScore(
                server_id="s2",
                axis="confidentiality",
                p_top=0.7,
                p_critical=0.6,
                p_danger=0.3,
            ),
            McpLlmAxisScore(
                server_id="s2",
                axis="integrity",
                p_top=0.6,
                p_critical=0.5,
                p_danger=0.2,
            ),
            # server s3
            McpLlmAxisScore(
                server_id="s3",
                axis="confidentiality",
                p_top=0.5,
                p_critical=0.7,
                p_danger=0.4,
            ),
            McpLlmAxisScore(
                server_id="s3",
                axis="integrity",
                p_top=0.4,
                p_critical=0.6,
                p_danger=0.3,
            ),
            # server s4
            McpLlmAxisScore(
                server_id="s4",
                axis="confidentiality",
                p_top=0.3,
                p_critical=0.8,
                p_danger=0.5,
            ),
            McpLlmAxisScore(
                server_id="s4",
                axis="integrity",
                p_top=0.2,
                p_critical=0.7,
                p_danger=0.4,
            ),
        ]
        db.add_all(axis_data)
        db.commit()

    # Override the FastAPI dependency
    from app.db import get_session as original_get_session  # noqa: E402

    app = router  # the router itself is a FastAPI app when used alone
    app.dependency_overrides[original_get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/risk/summary")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)

    data = resp.json()

    # Expected tier counts
    expected_tiers = {"low": 2, "medium": 1, "high": 1}
    if data["tiers"] != expected_tiers:
        print(f"FAIL: tier counts {data['tiers']} != {expected_tiers}")
        sys.exit(1)

    # Expected totals
    if data["total_servers"] != 5 or data["assessed_servers"] != 4:
        print("FAIL: total/assessed server counts mismatch")
        sys.exit(1)

    # Expected axis averages (computed manually)
    def avg(vals):
        return sum(vals) / len(vals)

    # Confidentiality averages across the 4 assessed servers
    conf_p_top = avg([0.9, 0.7, 0.5, 0.3])
    conf_p_critical = avg([0.5, 0.6, 0.7, 0.8])
    conf_p_danger = avg([0.2, 0.3, 0.4, 0.5])

    # Integrity averages across the 4 assessed servers
    integ_p_top = avg([0.8, 0.6, 0.4, 0.2])
    integ_p_critical = avg([0.4, 0.5, 0.6, 0.7])
    integ_p_danger = avg([0.1, 0.2, 0.3, 0.4])

    expected_axes = {
        "confidentiality": {
            "avg_p_top": conf_p_top,
            "avg_p_critical": conf_p_critical,
            "avg_p_danger": conf_p_danger,
        },
        "integrity": {
            "avg_p_top": integ_p_top,
            "avg_p_critical": integ_p_critical,
            "avg_p_danger": integ_p_danger,
        },
    }

    # Allow small floating‑point differences
    def close(a, b, eps=1e-6):
        return abs(a - b) < eps

    for axis, vals in expected_axes.items():
        got = data["axis_averages"].get(axis)
        if not got:
            print(f"FAIL: missing axis {axis}")
            sys.exit(1)
        for key in vals:
            if not close(got[key], vals[key]):
                print(f"FAIL: axis {axis} {key} {got[key]} != {vals[key]}")
                sys.exit(1)

    print("PASS")
    sys.exit(0)