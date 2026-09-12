import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()
logger = logging.getLogger(__name__)

write_service_url = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772")


class HealthMonitor:
    def __init__(self, poll_interval: int = 60, stuck_threshold_minutes: int = 5):
        self.poll_interval = poll_interval
        self.stuck_threshold = timedelta(minutes=stuck_threshold_minutes)
        self.alerted_jobs: set[str] = set()
        self.alerts_fired = 0
        self.cycles_run = 0
        self._data_source = None

    def set_data_source(self, data_source: list[dict[str, Any]] | None):
        self._data_source = data_source

    def _query_cadence_job_runs(self) -> list[dict[str, Any]]:
        if self._data_source is not None:
            return self._data_source
        try:
            response = requests.post(
                f"{write_service_url}/query",
                json={
                    "table": "cadence_job_runs",
                    "columns": ["id", "job", "status", "started_at", "finished_at", "rows_affected", "detail"]
                },
                timeout=5
            )
            response.raise_for_status()
            return response.json().get("rows", [])
        except Exception as e:
            logger.warning(f"Failed to query cadence_job_runs: {e}")
            return []

    def _send_heartbeat(self) -> None:
        try:
            requests.post(
                f"{write_service_url}/write",
                json={
                    "table": "service_health",
                    "rows": [{
                        "service": "cadence_job_health_consumer",
                        "last_heartbeat": datetime.utcnow().isoformat()
                    }]
                },
                timeout=5
            )
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

    def _send_alert(self, job: str, status: str, detail: Any) -> None:
        try:
            requests.post(
                f"{write_service_url}/write",
                json={
                    "table": "service_health",
                    "rows": [{
                        "service": "cadence_job_health_alert",
                        "status": "ALERT",
                        "meta": {"job": job, "status": status, "detail": detail}
                    }]
                },
                timeout=5
            )
        except Exception as e:
            logger.warning(f"Failed to send alert: {e}")

    def _should_alert(self, row: dict[str, Any]) -> bool:
        job_id = str(row.get("id", ""))
        if job_id in self.alerted_jobs:
            return False
        unhealthy = {"failed", "error", "timeout"}
        if row.get("status") in unhealthy:
            return True
        started_at_str = row.get("started_at")
        if started_at_str and row.get("finished_at") is None:
            try:
                started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                if datetime.utcnow() - started_at > self.stuck_threshold:
                    return True
            except (ValueError, TypeError):
                pass
        return False

    def cycle(self) -> list[dict[str, Any]]:
        self.cycles_run += 1
        self._send_heartbeat()
        rows = self._query_cadence_job_runs()
        alerts: list[dict[str, Any]] = []
        for row in rows:
            if self._should_alert(row):
                job = row.get("job", "unknown")
                status = row.get("status", "unknown")
                detail = row.get("detail", {})
                self._send_alert(job, status, detail)
                self.alerted_jobs.add(str(row.get("id", "")))
                alerts.append({"job": job, "status": status, "detail": detail})
                self.alerts_fired += 1
        return alerts

    def run_loop(self):
        while True:
            self.cycle()
            time.sleep(self.poll_interval)


def create_dispute(db: Session = Depends(get_session)):
    from .logic import create_dispute as logic_create_dispute
    return logic_create_dispute(db)


def send_heartbeat(db: Session = Depends(get_session)):
    from .logic import send_heartbeat as logic_send_heartbeat
    return logic_send_heartbeat(db)


def get_server_history(db: Session = Depends(get_session)):
    from .logic import get_server_history as logic_get_server_history
    return logic_get_server_history(db)


def api_get_dwell_time(db: Session = Depends(get_session)):
    from .logic import api_get_dwell_time as logic_api_get_dwell_time
    return logic_api_get_dwell_time(db)


def _direction(db: Session = Depends(get_session)):
    from .logic import _direction as logic_direction
    return logic_direction(db)


def get_signal_scores_distribution(db: Session = Depends(get_session)):
    from .logic import get_signal_scores_distribution as logic_get_signal_scores_distribution
    return logic_get_signal_scores_distribution(db)


def get_stub_session():
    from .logic import get_stub_session as logic_get_stub_session
    return logic_get_stub_session()


def normalize_advisory_feed(db: Session = Depends(get_session)):
    from .logic import normalize_advisory_feed as logic_normalize_advisory_feed
    return logic_normalize_advisory_feed(db)


def refill_anchor_data(db: Session = Depends(get_session)):
    from .logic import refill_anchor_data as logic_refill_anchor_data
    return logic_refill_anchor_data(db)


def run(db: Session = Depends(get_session)):
    from .logic import run as logic_run
    return logic_run(db)


def get_server_risk_tier(db: Session = Depends(get_session)):
    from .logic import get_server_risk_tier as logic_get_server_risk_tier
    return logic_get_server_risk_tier(db)


def get_risk_summary(db: Session = Depends(get_session)):
    from .logic import get_risk_summary as logic_get_risk_summary
    return logic_get_risk_summary(db)


def get_recent_decisions(db: Session = Depends(get_session)):
    from .logic import get_recent_decisions as logic_get_recent_decisions
    return logic_get_recent_decisions(db)


def get_snapshot(db: Session = Depends(get_session)):
    from .logic import get_snapshot as logic_get_snapshot
    return logic_get_snapshot(db)


def get_risk_tier_breakdown(db: Session = Depends(get_session)):
    from .logic import get_risk_tier_breakdown as logic_get_risk_tier_breakdown
    return logic_get_risk_tier_breakdown(db)


def run_monitor(db: Session = Depends(get_session)):
    from .logic import run_monitor as logic_run_monitor
    return logic_run_monitor(db)


def cve_search(db: Session = Depends(get_session)):
    from .logic import cve_search as logic_cve_search
    return logic_cve_search(db)


def get_family_coverage(db: Session = Depends(get_session)):
    from .logic import get_family_coverage as logic_get_family_coverage
    return logic_get_family_coverage(db)


def get_axis_scores_distribution(db: Session = Depends(get_session)):
    from .logic import get_axis_scores_distribution as logic_get_axis_scores_distribution
    return logic_get_axis_scores_distribution(db)


if __name__ == "__main__":
    print("Testing cadence_job_health_consumer...")

    monitor = HealthMonitor(poll_interval=60, stuck_threshold_minutes=5)

    now = datetime.utcnow()
    stuck_time = now - timedelta(minutes=10)

    mock_rows = [
        {
            "id": "job-001",
            "job": "batch_processor",
            "status": "failed",
            "started_at": (now - timedelta(minutes=30)).isoformat(),
            "finished_at": (now - timedelta(minutes=25)).isoformat(),
            "rows_affected": 0,
            "detail": {"error": "connection timeout"}
        },
        {
            "id": "job-002",
            "job": "data_sync",
            "status": "running",
            "started_at": stuck_time.isoformat(),
            "finished_at": None,
            "rows_affected": 0,
            "detail": {"message": "stuck job"}
        },
        {
            "id": "job-003",
            "job": "cleanup_task",
            "status": "completed",
            "started_at": (now - timedelta(minutes=10)).isoformat(),
            "finished_at": (now - timedelta(minutes=5)).isoformat(),
            "rows_affected": 1500,
            "detail": {"records_processed": 1500}
        }
    ]

    monitor.set_data_source(mock_rows)
    alerts = monitor.cycle()

    assert monitor.alerts_fired == 2, f"Expected 2 alerts, got {monitor.alerts_fired}"
    assert len(alerts) == 2, f"Expected 2 alerts returned, got {len(alerts)}"

    alert_jobs = {a["job"] for a in alerts}
    assert "batch_processor" in alert_jobs, "Expected alert for failed batch_processor"
    assert "data_sync" in alert_jobs, "Expected alert for stuck data_sync"

    ok_job_alerts = [a for a in alerts if a["job"] == "cleanup_task"]
    assert len(ok_job_alerts) == 0, "Should not alert for completed cleanup_task"

    print("PASS")