import datetime
import time
from typing import Callable, List

import requests
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import CadenceJobRun


class HealthMonitor:
    """
    Monitors `cadence_job_runs` for unhealthy job states and emits alerts.
    """

    def __init__(self, session_factory: Callable[[], Session] = get_session):
        self.session_factory = session_factory
        self.alerts_fired = 0

    def _post_heartbeat(self) -> None:
        payload = {
            "table": "service_health",
            "rows": {
                "service": "cadence_job_health_consumer",
                "last_heartbeat": datetime.datetime.utcnow().isoformat(),
            },
        }
        requests.post("http://127.0.0.1:8772/write", json=payload)

    def _post_alert(self, job_run: CadenceJobRun) -> None:
        payload = {
            "table": "service_health",
            "rows": {
                "service": "cadence_job_health_alert",
                "status": "ALERT",
                "meta": {
                    "job": job_run.job,
                    "status": job_run.status,
                    "detail": job_run.detail,
                },
            },
        }
        requests.post("http://127.0.0.1:8772/write", json=payload)

    def poll(self) -> None:
        """Execute a single monitoring cycle."""
        now = datetime.datetime.utcnow()
        five_minutes_ago = now - datetime.timedelta(minutes=5)

        with self.session_factory() as session:
            stmt = select(CadenceJobRun).where(
                or_(
                    CadenceJobRun.status.in_(["failed", "error", "timeout"]),
                    and_(
                        CadenceJobRun.started_at <= five_minutes_ago,
                        CadenceJobRun.finished_at.is_(None),
                    ),
                )
            )
            rows: List[CadenceJobRun] = session.execute(stmt).scalars().all()

            for job_run in rows:
                self._post_alert(job_run)
                self.alerts_fired += 1

        self._post_heartbeat()

    def run(self, interval: int = 60) -> None:
        """Continuously run the monitor, sleeping `interval` seconds between cycles."""
        while True:
            self.poll()
            time.sleep(interval)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Setup an in‑memory SQLite DB using the real model definitions.
    # ------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    # Override the FastAPI‑style dependency to use our test session.
    def test_session_factory() -> Session:
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # Insert mock data: one failed, one stuck, one healthy job run.
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    with SessionLocal() as s:
        failed = CadenceJobRun(
            job="job_failed",
            status="failed",
            started_at=now - datetime.timedelta(minutes=10),
            finished_at=now - datetime.timedelta(minutes=9),
            rows_affected=0,
            detail={"msg": "failure"},
        )
        stuck = CadenceJobRun(
            job="job_stuck",
            status="running",
            started_at=now - datetime.timedelta(minutes=10),
            finished_at=None,
            rows_affected=0,
            detail={},
        )
        ok = CadenceJobRun(
            job="job_ok",
            status="completed",
            started_at=now - datetime.timedelta(minutes=10),
            finished_at=now - datetime.timedelta(minutes=9),
            rows_affected=0,
            detail={},
        )
        s.add_all([failed, stuck, ok])
        s.commit()

    # ------------------------------------------------------------------- #
    # Mock the external write_service POST endpoint.
    # ------------------------------------------------------------------- #
    captured_posts: List[dict] = []


    def mock_post(url: str, json: dict):
        captured_posts.append(json)
        class MockResponse:
            status_code = 200

            def raise_for_status(self):
                pass

        return MockResponse()


    requests.post = mock_post  # type: ignore

    # ------------------------------------------------------------------- #
    # Run the monitor once and verify alerts.
    # ------------------------------------------------------------------- #
    monitor = HealthMonitor(session_factory=test_session_factory)
    monitor.poll()

    alerts = [
        p
        for p in captured_posts
        if p.get("rows", {}).get("service") == "cadence_job_health_alert"
    ]

    assert len(alerts) == 2, f"expected 2 alerts, got {len(alerts)}"
    print("PASS")