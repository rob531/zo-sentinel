#!/usr/bin/env python3
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from zo_sentinel.breaker_actions.breaker_action_base import BreakerActionBase
from zo_sentinel.breaker_actions.breaker_action_utils import (
    get_daemon_status,
    notify_slack,
    run_breaker_workflow,
)
from zo_sentinel.config import Config
from zo_sentinel.daemon_manager import DaemonManager
from zo_sentinel.models import BreakerActionResult, DaemonStatus

class InvestigateGateSchedulerAction(BreakerActionBase):
    """Breaker action to investigate gate_scheduler staleness."""

    def __init__(self, config: Config):
        super().__init__(config)
        self.logger = logging.getLogger(__name__)
        self.daemon_manager = DaemonManager(config)
        self.gate_scheduler_name = "gate_scheduler"
        self.staleness_threshold = timedelta(hours=18)

    def execute(self) -> BreakerActionResult:
        """Execute the investigation workflow for gate_scheduler staleness."""
        try:
            self.logger.info(
                f"Starting investigation for {self.gate_scheduler_name} staleness"
            )

            # Check current status
            status = get_daemon_status(self.daemon_manager, self.gate_scheduler_name)
            if status != DaemonStatus.STALE:
                return BreakerActionResult(
                    success=True,
                    message=f"{self.gate_scheduler_name} is not stale (status: {status.value})",
                )

            # Verify staleness duration
            last_update = self.daemon_manager.get_daemon_last_update(
                self.gate_scheduler_name
            )
            if not last_update:
                return BreakerActionResult(
                    success=False,
                    message=f"Could not determine last update time for {self.gate_scheduler_name}",
                )

            staleness_duration = datetime.utcnow() - last_update
            if staleness_duration < self.staleness_threshold:
                return BreakerActionResult(
                    success=True,
                    message=f"{self.gate_scheduler_name} stale duration ({staleness_duration}) is below threshold ({self.staleness_threshold})",
                )

            # Trigger investigation workflow
            workflow_id = run_breaker_workflow(
                self.config,
                "gate_scheduler_investigation",
                {
                    "daemon_name": self.gate_scheduler_name,
                    "staleness_duration": str(staleness_duration),
                    "last_update": last_update.isoformat(),
                },
            )

            # Notify relevant channels
            notify_slack(
                self.config,
                f"🚨 Investigation started for {self.gate_scheduler_name} staleness (duration: {staleness_duration})",
                channel="#build-alerts",
                workflow_id=workflow_id,
            )

            return BreakerActionResult(
                success=True,
                message=f"Investigation workflow triggered for {self.gate_scheduler_name} staleness",
                workflow_id=workflow_id,
            )

        except Exception as e:
            error_msg = f"Failed to investigate {self.gate_scheduler_name} staleness: {str(e)}"
            self.logger.exception(error_msg)
            notify_slack(
                self.config,
                f"❌ {error_msg}",
                channel="#build-alerts",
            )
            return BreakerActionResult(success=False, message=error_msg)

if __name__ == "__main__":
    config = Config()
    action = InvestigateGateSchedulerAction(config)
    result = action.execute()

    if result.success:
        logging.info(f"Action completed: {result.message}")
    else:
        logging.error(f"Action failed: {result.message}")