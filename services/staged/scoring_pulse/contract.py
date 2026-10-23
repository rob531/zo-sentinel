# services/staged/scoring_pulse/contract.py
from datetime import datetime, timedelta
from typing import Callable, List, Optional

import httpx
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from app.db import get_session
from app.models import Base  # noqa: F401  (ensures models are imported)

router = APIRouter()


# ----------------------------------------------------------------------
# Dependency that talks to the ZoComputer write service.
# In production it posts to http://127.0.0.1:8772/query.
# In tests it will be overridden with a stub.
# ----------------------------------------------------------------------
def get_query_service() -> Callable[[str, dict], List[dict]]:
    def _query(sql: str, params: dict) -> List[dict]:
        resp = httpx.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()
    return _query


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class JobCounts(BaseModel):
    running: int = 0
    success: int = 0
    failed: int = 0
    queued: int = 0


class PulseResponse(BaseModel):
    last_24h: int
    last_7d: int
    last_30d: int
    job_counts: JobCounts
    mean_rows_affected: float
    oldest_pending_age_s: Optional[int] = None


# ----------------------------------------------------------------------
# Endpoint implementation
# ----------------------------------------------------------------------
@router.get("/api/scoring/pulse", response_model=PulseResponse)
def get_scoring_pulse(
    _: get_session = Depends(get_session),  # DB session kept for consistency with other services
    query: Callable[[str, dict], List[dict]] = Depends(get_query_service),
) -> PulseResponse:
    now = datetime.utcnow()

    # ------------------------------------------------------------------
    # 1. Servers scored in the last 24h / 7d / 30d
    # ------------------------------------------------------------------
    periods = {
        "last_24h": now - timedelta(days=1),
        "last_7d": now - timedelta(days=7),
        "last_30d": now - timedelta(days=30),
    }
    scored_counts = {}
    for key, since in periods.items():
        rows = query(
            """
            SELECT COUNT(DISTINCT server_id) AS count
            FROM McpLlmAxisScore
            WHERE scored_at >= :since
            """,
            {"since": since},
        )
        scored_counts[key] = rows[0]["count"] if rows else 0

    # ------------------------------------------------------------------
    # 2. Cadence job run counts by status
    # ------------------------------------------------------------------
    rows = query(
        """
        SELECT status, COUNT(*) AS cnt
        FROM cadence_job_runs
        WHERE job = 'scoring'
        GROUP BY status
        """,
        {},
    )
    job_counts = {"running": 0, "success": 0, "failed": 0, "queued": 0}
    for r in rows:
        status = r["status"]
        if status in job_counts:
            job_counts[status] = r["cnt"]

    # ------------------------------------------------------------------
    # 3. Mean rows_affected per scoring run
    # ------------------------------------------------------------------
    rows = query(
        """
        SELECT AVG(rows_affected) AS avg
        FROM cadence_job_runs
        WHERE job = 'scoring'
        """,
        {},
    )
    mean_rows = float(rows[0]["avg"]) if rows and rows[0]["avg"] is not None else 0.0

    # ------------------------------------------------------------------
    # 4. Oldest pending job age (queued jobs)
    # ------------------------------------------------------------------
    rows = query(
        """
        SELECT MIN(started_at) AS min_started
        FROM cadence_job_runs
        WHERE job = 'scoring' AND status = 'queued'
        """,
        {},
    )
    if rows and rows[0]["min_started"]:
        oldest_age = int((now - rows[0]["min_started"]).total_seconds())
    else:
        oldest_age = None

    return PulseResponse(
        last_24h=scored_counts["last_24h"],
        last_7d=scored_counts["last_7d"],
        last_30d=scored_counts["last_30d"],
        job_counts=JobCounts(**job_counts),
        mean_rows_affected=mean_rows,
        oldest_pending_age_s=oldest_age,
    )


# ----------------------------------------------------------------------
# Self‑test (run with: python -m services.staged.scoring_pulse.contract)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------
    # Stub query service used only for the self‑test
    # ------------------------------------------------------------------
    class StubQueryService:
        def __init__(self) -> None:
            self.now = datetime.utcnow()
            self.job_runs = [
                {"status": "running", "rows_affected": 10, "started_at": self.now - timedelta(hours=1)},
                {"status": "success", "rows_affected": 20, "started_at": self.now - timedelta(days=2)},
                {"status": "failed", "rows_affected": 5, "started_at": self.now - timedelta(days=3)},
                {"status": "queued", "rows_affected": 0, "started_at": self.now - timedelta(hours=5)},
                {"status": "queued", "rows_affected": 0, "started_at": self.now - timedelta(hours=10)},
            ]
            self.scores = [
                {"server_id": 1, "scored_at": self.now - timedelta(hours=2)},
                {"server_id": 2, "scored_at": self.now - timedelta(days=2)},
                {"server_id": 3, "scored_at": self.now - timedelta(days=10)},
                {"server_id": 4, "scored_at": self.now - timedelta(days=20)},
                {"server_id": 5, "scored_at": self.now - timedelta(days=40)},
            ]

        def __call__(self, sql: str, params: dict) -> List[dict]:
            if "FROM McpLlmAxisScore" in sql:
                since = params.get("since")
                cnt = sum(1 for s in self.scores if s["scored_at"] >= since)
                return [{"count": cnt}]
            if "FROM cadence_job_runs" in sql and "GROUP BY status" in sql:
                from collections import Counter

                cnt = Counter(r["status"] for r in self.job_runs)
                return [{"status": st, "cnt": cnt[st]} for st in cnt]
            if "AVG(rows_affected)" in sql:
                avg = sum(r["rows_affected"] for r in self.job_runs) / len(self.job_runs)
                return [{"avg": avg}]
            if "MIN(started_at)" in sql:
                pending = [r["started_at"] for r in self.job_runs if r["status"] == "queued"]
                if pending:
                    return [{"min_started": min(pending)}]
                return []
            return []

    # ------------------------------------------------------------------
    # Build a minimal FastAPI app and override dependencies
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)

    # SQLite in‑memory session (required by the get_session dependency)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Ensure all model tables exist (even if they are not used in the test)
    Base.metadata.create_all(bind=engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_query_service] = lambda: StubQueryService()

    client = TestClient(app)

    resp = client.get("/api/scoring/pulse")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    total_jobs = sum(data["job_counts"].values())
    assert total_jobs == 5, f"Expected 5 jobs, got {total_jobs}"
    assert data["mean_rows_affected"] > 0, "Mean rows_affected should be > 0"

    print("PASS")