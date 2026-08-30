"""Dead organ report monitor service."""

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


class MonitorResult(BaseModel):
    status: str
    message: str


def get_dead_organ_reports(session: Session) -> list[dict]:
    """Fetch dead organ reports from mesh/pipeline store."""
    import httpx
    response = httpx.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM dead_organ_reports WHERE status = 'pending'"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json().get("results", [])


def count_pending_disputes(session: Session) -> int:
    """Count pending score disputes."""
    result = session.execute(
        text("SELECT COUNT(*) FROM McpScoreDispute WHERE status = 'pending'")
    )
    return result.scalar() or 0


def monitor_dead_organ_reports(session: Session) -> MonitorResult:
    """Monitor and report on dead organ reports."""
    try:
        reports = get_dead_organ_reports(session)
        pending_count = count_pending_disputes(session)
        if pending_count > 0:
            return MonitorResult(
                status="alert",
                message=f"Found {pending_count} pending disputes and {len(reports)} dead organ reports"
            )
        return MonitorResult(
            status="ok",
            message=f"Monitoring {len(reports)} dead organ reports"
        )
    except Exception as e:
        return MonitorResult(status="error", message=str(e))


def test(session: Session = Depends(get_session)) -> MonitorResult:
    """Self-test endpoint for dead organ report monitor."""
    return monitor_dead_organ_reports(session)


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return test()

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    print("Starting dead_organ_report_monitor on :8771")
    uvicorn.run(app, host="0.0.0.0", port=8771, log_level="warning")