from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional, List
from app.db import get_session
from app.models import CadenceJobRun
import sqlglot


app = FastAPI()


class DaemonSummary(BaseModel):
    name: str
    age_seconds: float
    status: str
    cycle_threshold: int
    is_stale: bool
    last_job_run_rows_affected: Optional[int]


class Aggregate(BaseModel):
    total_daemons: int
    stale_count: int
    healthy_count: int


class DaemonRosterResponse(BaseModel):
    daemons: List[DaemonSummary]
    aggregate: Aggregate


STALE_THRESHOLD = 300


def get_daemon_roster_summary() -> DaemonRosterResponse:
    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())

    health_sql = "SELECT service, status, last_heartbeat, meta FROM service_health"
    health_result = sqlglot.run("SELECT service, status, last_heartbeat, meta FROM service_health", read="postgres", write="sqlite")
    
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": health_sql, "db": "ZoComputer"},
            timeout=5
        )
        resp.raise_for_status()
        health_rows = resp.json().get("rows", [])
    except Exception:
        return DaemonRosterResponse(
            daemons=[],
            aggregate=Aggregate(total_daemons=0, stale_count=0, healthy_count=0)
        )

    daemons = []
    stale_count = 0
    healthy_count = 0

    for row in health_rows:
        name = row.get("service", "")
        status = row.get("status", "unknown")
        last_heartbeat_raw = row.get("last_heartbeat")

        if last_heartbeat_raw is None:
            continue

        if isinstance(last_heartbeat_raw, str):
            try:
                lh_dt = datetime.fromisoformat(last_heartbeat_raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    lh_dt = datetime.strptime(last_heartbeat_raw, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
        elif isinstance(last_heartbeat_raw, (int, float)):
            lh_dt = datetime.fromtimestamp(last_heartbeat_raw, tz=timezone.utc)
        else:
            continue

        age_seconds = now_ts - int(lh_dt.timestamp())
        is_stale = age_seconds > STALE_THRESHOLD

        if is_stale:
            stale_count += 1
        elif status and status not in ("unknown", ""):
            healthy_count += 1

        job_sql = f"SELECT rows_affected FROM cadence_job_runs WHERE job = '{name}' ORDER BY finished_at DESC LIMIT 1"
        
        try:
            job_resp = requests.post(
                "http://127.0.0.1:8772/query",
                json={"sql": job_sql, "db": "ZoComputer"},
                timeout=5
            )
            job_resp.raise_for_status()
            job_rows = job_resp.json().get("rows", [])
            rows_affected = job_rows[0]["rows_affected"] if job_rows else None
        except Exception:
            rows_affected = None

        daemons.append(DaemonSummary(
            name=name,
            age_seconds=age_seconds,
            status=status,
            cycle_threshold=STALE_THRESHOLD,
            is_stale=is_stale,
            last_job_run_rows_affected=rows_affected
        ))

    return DaemonRosterResponse(
        daemons=daemons,
        aggregate=Aggregate(
            total_daemons=len(daemons),
            stale_count=stale_count,
            healthy_count=healthy_count
        )
    )


@app.get("/api/daemon/roster")
def get_daemon_roster() -> DaemonRosterResponse:
    return get_daemon_roster_summary()


if __name__ == "__main__":
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.main import app as fastapi_app
    from datetime import timedelta

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    stale_time = now - timedelta(seconds=400)
    healthy_time1 = now - timedelta(seconds=100)
    healthy_time2 = now - timedelta(seconds=50)

    conn = engine.connect()
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS service_health (
            service TEXT,
            status TEXT,
            last_heartbeat TEXT,
            meta TEXT
        )
    """))
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS cadence_job_runs (
            id INTEGER PRIMARY KEY,
            job TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            rows_affected INTEGER,
            detail TEXT
        )
    """))
    conn.commit()

    conn.execute(sqlalchemy.text(
        "INSERT INTO service_health VALUES (?, ?, ?, ?)"
    ), ["stale_daemon", "running", stale_time.isoformat(), "{}"])
    conn.execute(sqlalchemy.text(
        "INSERT INTO service_health VALUES (?, ?, ?, ?)"
    ), ["healthy_daemon_1", "healthy", healthy_time1.isoformat(), "{}"])
    conn.execute(sqlalchemy.text(
        "INSERT INTO service_health VALUES (?, ?, ?, ?)"
    ), ["healthy_daemon_2", "healthy", healthy_time2.isoformat(), "{}"])
    conn.commit()
    conn.close()

    def override_get_session():
        return session_factory()

    fastapi_app.dependency_overrides[get_session] = override_get_session

    with fastapi_app.test_client() as client:
        response = client.get("/api/daemon/roster")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()

        assert data["aggregate"]["total_daemons"] == 3, f"Expected total_daemons=3, got {data['aggregate']['total_daemons']}"
        assert data["aggregate"]["stale_count"] == 1, f"Expected stale_count=1, got {data['aggregate']['stale_count']}"
        assert data["aggregate"]["healthy_count"] == 2, f"Expected healthy_count=2, got {data['aggregate']['healthy_count']}"

        stale_daemons = [d for d in data["daemons"] if d["is_stale"] is True]
        assert len(stale_daemons) >= 1, f"Expected at least one daemon with is_stale=True"

        print("PASS")