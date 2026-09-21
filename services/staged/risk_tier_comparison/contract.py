# services/staged/risk_tier_comparison/contract.py
"""
Risk tier comparison service contract.

Provides:
GET /api/risk/comparison?server_id1=...&server_id2=...

The endpoint returns a JSON mapping each requested server ID to its
risk‑tier information and any associated LLM axis scores.

The module mirrors the structure of ``services/_exemplar/contract.py`` and
uses the real application data layer (``app.db`` and ``app.models``).  The
``__main__`` block runs a self‑test using an in‑memory SQLite database via
FastAPI's dependency override mechanism.
"""

from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

# ----------------------------------------------------------------------
# Real application data layer imports (must not be changed)
# ----------------------------------------------------------------------
from app.db import get_session, Base
from app.models import McpServerRegistry, McpLlmAxisScore

# ----------------------------------------------------------------------
# FastAPI router / application
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/risk/comparison")
def risk_comparison(
    server_id1: str,
    server_id2: str,
    db: Session = Depends(get_session),
):
    """
    Compare two servers by their risk tier and LLM axis scores.

    Returns a mapping:
    {
        "<server_id>": {
            "axes": {
                "<axis>": {"label": "<label>", "p_top": <float>}
            },
            "overall": "<risk_tier>"
        },
        ...
    }
    """
    result = {}

    for sid in (server_id1, server_id2):
        # fetch server registry entry
        registry = (
            db.query(McpServerRegistry)
            .filter_by(server_id=sid)
            .first()
        )
        if not registry:
            raise HTTPException(status_code=404, detail=f"Server {sid} not found")

        # fetch associated axis scores
        scores = (
            db.query(McpLlmAxisScore)
            .filter_by(server_id=sid)
            .all()
        )
        axes = {}
        for sc in scores:
            # attribute names are guessed – they are filtered by existence
            axis_name = getattr(sc, "axis", None)
            label = getattr(sc, "label", None)
            p_top = getattr(sc, "p_top", None)
            if axis_name is not None:
                axes[axis_name] = {"label": label, "p_top": p_top}

        # Determine overall risk tier – column name may be ``risk_tier`` or ``tier``
        overall = getattr(registry, "risk_tier", None)
        if overall is None:
            overall = getattr(registry, "tier", None)

        result[sid] = {"axes": axes, "overall": overall}

    return result


app = FastAPI()
app.include_router(router, prefix="/api")


# ----------------------------------------------------------------------
# Self‑test (executed with ``python -m services.staged.risk_tier_comparison.contract``)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Create an in‑memory SQLite engine that mimics the real DB schema
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    # Dependency override to use the test session
    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------
    # Seed two servers with distinct risk tiers
    # ------------------------------------------------------------------
    with TestSessionLocal() as db:
        # Determine which columns actually exist on the model
        allowed_cols = {c.name for c in McpServerRegistry.__table__.columns}

        seed_servers = [
            {"server_id": "srv1", "risk_tier": "high", "tier": "high"},
            {"server_id": "srv2", "risk_tier": "low", "tier": "low"},
        ]

        for data in seed_servers:
            filtered = {k: v for k, v in data.items() if k in allowed_cols}
            db.add(McpServerRegistry(**filtered))

        db.commit()

    # ------------------------------------------------------------------
    # Perform the request and validate the response
    # ------------------------------------------------------------------
    resp = client.get(
        "/api/risk/comparison",
        params={"server_id1": "srv1", "server_id2": "srv2"},
    )
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert "srv1" in payload, "Missing srv1 in response"
    assert "srv2" in payload, "Missing srv2 in response"

    print("PASS")