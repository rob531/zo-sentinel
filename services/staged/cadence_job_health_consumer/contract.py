from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import Depends
from app.db import get_session
from app.models import CadenceJobRun
from pydantic import BaseModel
import requests
import time

class JobHealthAlert(BaseModel):
    job: str
    status: str
    detail: Optional[Dict]

class HealthMonitor:
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session
        self.write_service_url = "http://127.0.0.1:8772/query"

    def _post_to_write_service(self, table: str, rows: List[Dict]):
        payload = {"table": table, "rows": rows}
        try:
            response = requests.post(self.write_service_url, json=payload)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Error posting to write_service: {e}")

    def _check_for_alerts(self) -> List[JobHealthAlert]:
        alerts = []
        five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)

        # Check for failed/error/timeout jobs
        failed_jobs = self.session.query(CadenceJobRun).filter(
            CadenceJobRun.status.in_(['failed', 'error', 'timeout'])
        ).all()

        for job in failed_jobs:
            alerts.append(JobHealthAlert(
                job=job.job,
                status=job.status,
                detail=job.detail
            ))

        # Check for stuck jobs (started >5min ago with no finished_at)
        stuck_jobs = self.session.query(CadenceJobRun).filter(
            CadenceJobRun.started_at <= five_minutes_ago,
            CadenceJobRun.finished_at.is_(None)
        ).all()

        for job in stuck_jobs:
            alerts.append(JobHealthAlert(
                job=job.job,
                status="stuck",
                detail=job.detail
            ))

        return alerts

    def run_cycle(self):
        # Heartbeat
        self._post_to_write_service(
            table="service_health",
            rows=[{
                "service": "cadence_job_health_consumer",
                "last_heartbeat": datetime.utcnow().isoformat()
            }]
        )

        # Check for alerts
        alerts = self._check_for_alerts()
        for alert in alerts:
            self._post_to_write_service(
                table="service_health",
                rows=[{
                    "service": "cadence_job_health_alert",
                    "status": "ALERT",
                    "meta": {
                        "job": alert.job,
                        "status": alert.status,
                        "detail": alert.detail
                    }
                }]
            )

    def monitor(self):
        while True:
            self.run_cycle()
            time.sleep(60)

def get_health_monitor(session: Session = Depends(get_session)) -> HealthMonitor:
    return HealthMonitor(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create test app with dependency override
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    from app.models import Base
    Base.metadata.create_all(test_engine)

    test_session = SessionLocal()
    test_session.add_all([
        CadenceJobRun(
            job="test_job_1",
            status="failed",
            started_at=datetime.utcnow() - timedelta(minutes=10),
            finished_at=datetime.utcnow() - timedelta(minutes=5),
            rows_affected=0,
            detail={"error": "test error"}
        ),
        CadenceJobRun(
            job="test_job_2",
            status="running",
            started_at=datetime.utcnow() - timedelta(minutes=6),
            finished_at=None,
            rows_affected=0,
            detail={}
        ),
        CadenceJobRun(
            job="test_job_3",
            status="completed",
            started_at=datetime.utcnow() - timedelta(minutes=2),
            finished_at=datetime.utcnow() - timedelta(seconds=30),
            rows_affected=100,
            detail={}
        )
    ])
    test_session.commit()

    # Test the monitor
    monitor = HealthMonitor(test_session)
    alerts = monitor._check_for_alerts()

    assert len(alerts) == 2
    assert alerts[0].job == "test_job_1"
    assert alerts[1].job == "test_job_2"

    print("PASS")