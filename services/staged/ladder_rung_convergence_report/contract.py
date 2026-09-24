# services/staged/ladder_rung_convergence_report/contract.py
from __future__ import annotations

from collections import defaultdict
from statistics import mean, pstdev
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports (must not be mocked here)
from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class AxisStats(BaseModel):
    mean: float = Field(..., description="Mean of the scores for the axis")
    stddev: float = Field(..., description="Population standard deviation of the scores for the axis")


class ConvergenceReport(BaseModel):
    wave: str = Field(..., description="Scoring wave identifier")
    axes: Dict[str, AxisStats] = Field(..., description="Mapping of axis name to its statistics")


@router.get(
    "/scorer/convergence",
    response_model=ConvergenceReport,
    summary="Compute axis‑wise convergence statistics for a given scoring wave",
)
def get_convergence_report(
    wave: str = Query(..., description="Scoring wave identifier (matches McpLlmAxisScore.model_version)"),
    session: Session = Depends(get_session),
):
    # Retrieve all scores for the requested wave
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.model_version == wave)
        .all()
    )
    if not scores:
        raise HTTPException(status_code=404, detail=f"No scores found for wave {wave}")

    # Group scores by axis name
    grouped: Dict[str, list[float]] = defaultdict(list)
    for s in scores:
        # Use p_top as the primary score metric
        if s.p_top is not None:
            grouped[s.axis_name].append(float(s.p_top))

    # Compute statistics
    axes_stats: Dict[str, AxisStats] = {}
    for axis, values in grouped.items():
        if not values:
            continue
        axes_stats[axis] = AxisStats(
            mean=mean(values),
            stddev=pstdev(values) if len(values) > 1 else 0.0,
        )

    return ConvergenceReport(wave=wave, axes=axes_stats)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.ladder_rung_convergence_report.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real get_session dependency)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed data: 3 waves (model_version "1","2","3") with 2 servers each,
    # each server provides two axis scores ("accuracy","latency").
    # Values are chosen so that stddev > 0 for at least one axis.
    # ------------------------------------------------------------------- #
    with TestingSessionLocal() as db:
        # Create servers
        servers = []
        for wave in ("1", "2", "3"):
            for i in range(2):
                srv = McpServerRegistry(
                    server_id=f"svr-{wave}-{i}",
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
                    verdict_reasoning=None,
                )
                db.add(srv)
                servers.append(srv)

        db.flush()  # assign PKs if any

        # Create axis scores
        for wave in ("1", "2", "3"):
            for srv in servers:
                if not srv.server_id.startswith(f"svr-{wave}-"):
                    continue
                # accuracy scores differ per server to give stddev
                acc_score = 0.8 if srv.server_id.endswith("-0") else 0.6
                # latency scores are constant (stddev 0)
                lat_score = 0.5
                for axis, score in (("accuracy", acc_score), ("latency", lat_score)):
                    ax = McpLlmAxisScore(
                        adapter_sha256="dummy",
                        axis_name=axis,
                        decision_rule_version="v1",
                        escalated=False,
                        escalated_to=None,
                        id=None,
                        label="dummy",
                        label_index=0,
                        model_version=wave,
                        p_critical=None,
                        p_danger=None,
                        p_top=score,
                        probs=None,
                        scored_at=None,
                        server_id=srv.server_id,
                    )
                    db.add(ax)
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform acceptance test for wave=1
    # ------------------------------------------------------------------- #
    resp = client.get("/api/scorer/convergence", params={"wave": "1"})
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    # Ensure stddev > 0 for at least one axis (accuracy)
    try:
        acc_std = data["axes"]["accuracy"]["stddev"]
        if acc_std <= 0:
            raise AssertionError
    except Exception:
        print("FAIL: stddev for 'accuracy' not > 0", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)