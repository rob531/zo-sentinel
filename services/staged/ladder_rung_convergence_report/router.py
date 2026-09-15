"""ladder_rung_convergence_report router."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_convergence_report

router = APIRouter(prefix="/api", tags=["ladder_rung_convergence_report"])


@router.get("/scorer/convergence")
def convergence_endpoint(
    wave: int = Query(..., ge=0),
    db: Session = Depends(get_session),
):
    """
    Return axis‑wise convergence statistics for a given scoring wave.
    """
    try:
        return get_convergence_report(db, wave)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from contextlib import contextmanager

    from app.models import (
        Base,
        McpServerRegistry,
        McpLlmAxisScore,
    )

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    @contextmanager
    def override_get_session() -> Session:  # type: ignore
        """Yield a fresh DB session bound to the in‑memory SQLite engine."""
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # Seed test data: 3 waves, 2 servers per wave, 2 axes with varying scores
    # --------------------------------------------------------------------- #
    with SessionLocal() as db:
        # create servers (reuse same servers across waves)
        servers = [
            McpServerRegistry(
                server_id="srv-1",
                name="server‑one",
                registry_source="test",
                risk_tier="low",
                verdict="unknown",
                confidence=0.5,
                description="test server 1",
                first_seen="2023-01-01T00:00:00Z",
                last_seen="2023-01-01T00:00:00Z",
                last_scanned="2023-01-01T00:00:00Z",
                last_assessed="2023-01-01T00:00:00Z",
                meta="{}",
                scan_count=0,
                trust_score=0.0,
                url="http://example.com/1",
                verdict_reasoning="",
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="server‑two",
                registry_source="test",
                risk_tier="low",
                verdict="unknown",
                confidence=0.5,
                description="test server 2",
                first_seen="2023-01-01T00:00:00Z",
                last_seen="2023-01-01T00:00:00Z",
                last_scanned="2023-01-01T00:00:00Z",
                last_assessed="2023-01-01T00:00:00Z",
                meta="{}",
                scan_count=0,
                trust_score=0.0,
                url="http://example.com/2",
                verdict_reasoning="",
            ),
        ]
        db.add_all(servers)
        db.flush()  # assign PKs if needed

        # create axis scores
        axes = ["security", "performance"]
        for wave in (1, 2, 3):
            for srv in servers:
                for axis in axes:
                    # vary the probability to ensure non‑zero stddev
                    prob = 0.2 * wave if axis == "security" else 0.5
                    score = McpLlmAxisScore(
                        server_id=srv.server_id,
                        axis_name=axis,
                        label_index=wave,  # use label_index as the wave identifier
                        adapter_sha256="deadbeef",
                        decision_rule_version="v1",
                        escalated=False,
                        escalated_to=None,
                        id=None,
                        label="test",
                        model_version="v1",
                        p_critical=0.0,
                        p_danger=0.0,
                        p_top=0.0,
                        probs=json.dumps([prob, 1 - prob]),
                        scored_at="2023-01-01T00:00:00Z",
                    )
                    db.add(score)
        db.commit()

    # --------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Perform test request
    # --------------------------------------------------------------------- #
    resp = client.get("/api/scorer/convergence?wave=1")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
    data = resp.json()
    assert data["wave"] == 1, "Wave mismatch in response"
    # ensure at least one axis reports a stddev > 0
    stddevs = [axis_info["stddev"] for axis_info in data["axes"].values()]
    assert any(s > 0 for s in stddevs), "All stddevs are zero"
    print("PASS")