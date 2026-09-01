# services/staged/definition_history_pipeline_status_dashboard/contract.py
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

# Real data layer imports (must remain unchanged for production)
from app.db import get_session

router = APIRouter(prefix="/api")


@router.get("/definition_history/pipeline_status")
def pipeline_status(db: Session = Depends(get_session)):
    """
    Returns the count of definition‑history entries per pipeline status
    for the last 30 days.
    """
    cutoff = datetime.utcnow() - timedelta(days=30)
    stmt = text(
        """
        SELECT pipeline_status AS status, COUNT(*) AS count
        FROM mcp_definition_history
        WHERE created_at >= :cutoff
        GROUP BY pipeline_status
        """
    )
    rows = db.execute(stmt, {"cutoff": cutoff}).fetchall()
    return {
        "statuses": [
            {"status_name": row[0], "count": row[1]} for row in rows
        ]
    }


# ----------------------------------------------------------------------
# Self‑test (run with: python -m services.staged.definition_history_pipeline_status_dashboard.contract)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------
    # Stub session that mimics the DB response required for the test.
    # ------------------------------------------------------------------
    class _StubSession:
        def execute(self, *_args, **_kwargs):
            class _Result:
                def fetchall(self):
                    # three distinct statuses with deterministic counts
                    return [
                        ("queued", 5),
                        ("running", 12),
                        ("failed", 3),
                    ]

            return _Result()

    # ------------------------------------------------------------------
    # Build a minimal FastAPI app, include the router, and override the
    # dependency to use the stub session.
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: _StubSession()

    client = TestClient(app)
    resp = client.get("/api/definition_history/pipeline_status")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert "statuses" in payload, "Missing 'statuses' key"
    assert len(payload["statuses"]) == 3, "Expected three status entries"
    # Verify one known entry
    assert any(
        entry["status_name"] == "running" and entry["count"] == 12
        for entry in payload["statuses"]
    ), "Expected status 'running' with count 12"

    print("PASS")
    sys.exit(0)