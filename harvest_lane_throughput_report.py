from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import requests

from app.db import get_session
from app.models import CadenceJobRuns

router = APIRouter(prefix="/reporting", tags=["reporting"])

class LaneThroughput(BaseModel):
    source: str
    total_servers: int
    scored_count: int
    throughput_per_day: float
    avg_score_latency_hours: float

class HarvestLaneThroughputResponse(BaseModel):
    period_start: str
    period_end: str
    lanes: List[LaneThroughput]
    total_throughput: int

def query_mesh(sql: str, params: Optional[dict] = None) -> List[dict]:
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params or {}},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception:
        return []

@router.get("/harvest-lane-throughput", response_model=HarvestLaneThroughputResponse)
def get_harvest_lane_throughput(
    days: int = Query(default=7, ge=1, le=90),
    session = Depends(get_session)
) -> HarvestLaneThroughputResponse:
    now = datetime.now(timezone.utc)
    period_end = now
    period_start = now - timedelta(days=days)

    cadence_rows = query_mesh(
        """SELECT job_name, started_at, completed_at, status
           FROM cadence_job_runs
           WHERE started_at >= %s AND started_at <= %s
           AND job_name LIKE 'harvest_%'""",
        {"period_start": period_start.isoformat(), "period_end": period_end.isoformat()}
    )

    registry_rows = query_mesh(
        """SELECT source, COUNT(*) as total_servers
           FROM mcp_server_registry
           WHERE created_at >= %s AND created_at <= %s
           GROUP BY source""",
        {"period_start": period_start.isoformat(), "period_end": period_end.isoformat()}
    )

    source_counts = {row["source"]: row["total_servers"] for row in registry_rows}

    scored_counts = {}
    for row in cadence_rows:
        source = row["job_name"].replace("harvest_", "").replace("_scoring", "")
        scored_counts[source] = scored_counts.get(source, 0) + 1

    all_sources = set(source_counts.keys()) | set(scored_counts.keys())
    total_throughput = 0
    lanes = []

    for source in sorted(all_sources):
        total = source_counts.get(source, 0)
        scored = scored_counts.get(source, 0)
        throughput = scored / days if days > 0 else 0.0
        avg_latency = 0.0

        job_runs = session.query(CadenceJobRuns).filter(
            CadenceJobRuns.job_name.like(f"%{source}%")
        ).all()
        if job_runs:
            latencies = []
            for run in job_runs:
                if run.started_at and run.completed_at:
                    delta = (run.completed_at - run.started_at).total_seconds() / 3600
                    latencies.append(delta)
            if latencies:
                avg_latency = sum(latencies) / len(latencies)

        lanes.append(LaneThroughput(
            source=source,
            total_servers=total,
            scored_count=scored,
            throughput_per_day=round(throughput, 4),
            avg_score_latency_hours=round(avg_latency, 2)
        ))
        total_throughput += scored

    return HarvestLaneThroughputResponse(
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        lanes=lanes,
        total_throughput=total_throughput
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import get_session

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    from app.models import CadenceJobRuns
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    for i, source in enumerate(["npm", "github", "pypi"]):
        for j in range(2):
            run = CadenceJobRuns(
                job_name=f"harvest_{source}_scoring",
                started_at=now - timedelta(days=j),
                completed_at=now - timedelta(days=j) + timedelta(hours=2),
                status="completed"
            )
            test_session.add(run)
    test_session.commit()

    app = __import__("app")
    app.app.router = router
    app.app.dependency_overrides[get_session] = lambda: test_session
    client = TestClient(app.app)

    response = client.get("/reporting/harvest-lane-throughput?days=7")
    assert response.status_code == 200, f"Status {response.status_code}"
    data = response.json()

    assert "lanes" in data, "Missing lanes"
    assert len(data["lanes"]) > 0, "Lanes empty"
    for lane in data["lanes"]:
        assert "source" in lane, "Missing source field"
        assert lane["throughput_per_day"] >= 0, f"Negative throughput: {lane['throughput_per_day']}"

    print("PASS")