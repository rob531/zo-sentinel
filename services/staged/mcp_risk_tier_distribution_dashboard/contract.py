"""
services.staged.mcp_risk_tier_distribution_dashboard.contract
"""

from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Real data layer imports (must remain unchanged for production)
from app.db import get_session, Base
from app.models import McpServerRegistry  # type: ignore

router = APIRouter(prefix="/dashboard")


@router.get("/risk/distribution", response_class=HTMLResponse)
def get_risk_distribution(db: Session = Depends(get_session)):
    """
    Returns a simple HTML page that visualises the distribution of servers
    across risk tiers.
    """
    # Aggregate counts per risk tier
    try:
        tier_counts = (
            db.query(McpServerRegistry.risk_tier, func.count())
            .group_by(McpServerRegistry.risk_tier)
            .all()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Build a very small HTML page with a placeholder chart
    rows = "\n".join(
        f"<li>{tier or 'Unknown'}: {count}</li>" for tier, count in tier_counts
    )
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Risk Tier Distribution</title>
        <style>
            #chart {{ font-family: Arial, sans-serif; margin: 20px; }}
        </style>
    </head>
    <body>
        <h1>Risk Tier Distribution</h1>
        <div id="chart">
            <ul>
                {rows}
            </ul>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.mcp_risk_tier_distribution_dashboard.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a throw‑away FastAPI app with the router attached
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for the test only)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)

    # Create tables in the temporary SQLite DB
    Base.metadata.create_all(bind=engine)

    # Dependency override to use the temporary session
    def _override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Seed the temporary DB with deterministic data
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        tiers = ["Low", "Medium", "High", "Critical", "Info"]
        for i, tier in enumerate(tiers, start=1):
            rec = McpServerRegistry()
            # Populate only attributes that actually exist on the model
            if hasattr(rec, "server_id"):
                setattr(rec, "server_id", f"server-{i}")
            if hasattr(rec, "risk_tier"):
                setattr(rec, "risk_tier", tier)
            db.add(rec)
        db.commit()

    # ------------------------------------------------------------------- #
    # Execute the request against the test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/dashboard/risk/distribution")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    # Verify that each tier appears in the rendered HTML
    for tier in ["Low", "Medium", "High", "Critical", "Info"]:
        assert tier in resp.text, f"Missing tier {tier} in response"
    print("PASS")