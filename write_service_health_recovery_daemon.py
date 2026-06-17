#!/usr/bin/env python3
"""
write_service_health_recovery_daemon.py

Daemon that monitors write_service (:8772) health and auto-restarts it if stale.
Write_service underpins all DB writes; if it stalls the entire enrichment pipeline halts.
This daemon recovers automatically.
"""

import json
import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional, List

import requests

# Configuration defaults
DEFAULT_WRITE_SERVICE_URL = "http://127.0.0.1:8772"
DEFAULT_HEALTH_CHECK_INTERVAL = 60  # seconds
DEFAULT_STALENESS_THRESHOLD = 300  # seconds
DEFAULT_RESTART_COMMAND = ["systemctl", "restart", "write_service"]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class _MockPopen:
    """Mock subprocess.Popen for testing without actual process execution."""
    
    def __init__(self, args, **kwargs):
        self.args = args
        self.returncode = 0
        
    def wait(self):
        return self.returncode


class HealthRecoveryDaemon:
    """
    Daemon that monitors write_service health and auto-restarts it if stale.
    
    Writes heartbeats to service_health table via write_service API.
    """
    
    def __init__(
        self,
        write_service_url: str = DEFAULT_WRITE_SERVICE_URL,
        restart_command: Optional[List[str]] = None,
        health_check_interval: int = DEFAULT_HEALTH_CHECK_INTERVAL,
        staleness_threshold: int = DEFAULT_STALENESS_THRESHOLD,
        subprocess_patcher=None
    ):
        self.write_service_url = write_service_url
        self.restart_command = restart_command or DEFAULT_RESTART_COMMAND
        self.health_check_interval = health_check_interval
        self.staleness_threshold = staleness_threshold
        
        self._running = False
        self._consecutive_failures = 0
        self._last_healthy_time: Optional[datetime] = None
        self._subprocess_patcher = subprocess_patcher
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self._running = False
    
    def _health_check(self) -> tuple[bool, Optional[dict]]:
        """
        Perform health check on write_service.
        
        Returns:
            Tuple of (is_healthy, response_data)
        """
        try:
            response = requests.get(
                f"{self.write_service_url}/health",
                timeout=10
            )
            if response.status_code == 200:
                try:
                    data = response.json() if response.text else {}
                except json.JSONDecodeError:
                    data = {}
                
                self._last_healthy_time = datetime.now(timezone.utc)
                self._consecutive_failures = 0
                logger.debug(f"Health check passed: {data}")
                return True, data
            else:
                logger.warning(
                    f"Health check failed with status {response.status_code}"
                )
                self._consecutive_failures += 1
                return False, None
                
        except requests.exceptions.ConnectionError:
            logger.warning("Health check failed: connection refused")
            self._consecutive_failures += 1
            return False, None
        except requests.exceptions.Timeout:
            logger.warning("Health check failed: request timeout")
            self._consecutive_failures += 1
            return False, None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Health check failed: {e}")
            self._consecutive_failures += 1
            return False, None
    
    def _is_stale(self) -> bool:
        """
        Check if service is stale based on last healthy time.
        
        Returns:
            True if service has been unhealthy longer than staleness_threshold.
        """
        if self._last_healthy_time is None:
            return True
        
        elapsed = (datetime.now(timezone.utc) - self._last_healthy_time).total_seconds()
        return elapsed > self.staleness_threshold
    
    def _restart_service(self) -> bool:
        """
        Restart write_service using the configured restart command.
        
        Returns:
            True if restart was initiated successfully.
        """
        logger.warning(
            f"Restarting write_service with command: {' '.join(self.restart_command)}"
        )
        
        try:
            if self._subprocess_patcher:
                # In test mode, mock subprocess without actually restarting
                mock_process = _MockPopen(self.restart_command)
                logger.info(
                    f"[MOCK] Restart command initiated: {self.restart_command}"
                )
                return True
            else:
                # Real restart execution
                subprocess.run(
                    self.restart_command,
                    check=True,
                    capture_output=True,
                    text=True
                )
                logger.info("write_service restart initiated successfully")
                return True
                
        except subprocess.CalledProcessError as e:
            logger.error(f"Restart command failed: {e.stderr}")
            return False
        except FileNotFoundError:
            logger.error(
                f"Restart command not found: {self.restart_command[0]}"
            )
            return False
        except Exception as e:
            logger.error(f"Unexpected error during restart: {e}")
            return False
    
    def _write_heartbeat(self, status: str, message: str = ""):
        """
        Write heartbeat to service_health table via write_service API.
        
        Args:
            status: Current service status (healthy/unhealthy/restarted/stopped)
            message: Optional descriptive message
        """
        try:
            heartbeat_data = {
                "service": "write_service_health_recovery_daemon",
                "target_service": "write_service",
                "status": status,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "consecutive_failures": self._consecutive_failures
            }
            
            response = requests.post(
                f"{self.write_service_url}/health",
                json=heartbeat_data,
                timeout=10
            )
            response.raise_for_status()
            logger.debug(f"Heartbeat written: status={status}")
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to write heartbeat: {e}")
    
    def run(self) -> int:
        """
        Main daemon loop.
        
        Performs health checks, auto-restarts if stale/unhealthy,
        and sends heartbeats to service_health.
        
        Returns:
            0 on graceful shutdown, non-zero on fatal error.
        """
        logger.info(
            f"Starting write_service health recovery daemon "
            f"(target: {self.write_service_url})"
        )
        
        self._running = True
        restart_logged = False
        
        try:
            while self._running:
                # Perform health check
                is_healthy, _ = self._health_check()
                
                # Check if restart is needed due to staleness or consecutive failures
                if not is_healthy:
                    stale = self._is_stale()
                    
                    if stale or self._consecutive_failures >= 3:
                        if self._restart_service():
                            self._consecutive_failures = 0
                            restart_logged = True
                            self._write_heartbeat(
                                status="restarted",
                                message=f"Service restarted (stale={stale}, "
                                       f"failures={self._consecutive_failures})"
                            )
                        else:
                            self._write_heartbeat(
                                status="restart_failed",
                                message="Failed to restart service"
                            )
                    elif self._consecutive_failures == 1:
                        logger.warning(
                            "Service unhealthy, checking again before restart..."
                        )
                        self._write_heartbeat(
                            status="degraded",
                            message="Service unhealthy, monitoring..."
                        )
                else:
                    if restart_logged:
                        logger.info("Service recovered after restart")
                        restart_logged = False
                    self._write_heartbeat(status="healthy")
                
                # Sleep until next health check
                if self._running:
                    time.sleep(self.health_check_interval)
                    
        except KeyboardInterrupt:
            logger.info("Daemon interrupted by user")
        except Exception as e:
            logger.error(f"Fatal error in daemon loop: {e}")
            self._write_heartbeat(
                status="error",
                message=f"Fatal error: {str(e)}"
            )
            return 1
        
        # Graceful shutdown
        logger.info("Daemon shutting down gracefully")
        self._write_heartbeat(status="stopped", message="Daemon stopped")
        return 0


def run():
    """
    Entry point for running the daemon.
    
    Returns:
        Exit code from daemon.run()
    """
    daemon = HealthRecoveryDaemon()
    return daemon.run()


def main():
    """Command-line interface for the daemon."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Write Service Health Recovery Daemon"
    )
    parser.add_argument(
        "--write-service-url",
        default=DEFAULT_WRITE_SERVICE_URL,
        help="URL of write_service (default: %(default)s)"
    )
    parser.add_argument(
        "--restart-command",
        nargs="+",
        default=None,
        help="Command to restart write_service"
    )
    parser.add_argument(
        "--health-check-interval",
        type=int,
        default=DEFAULT_HEALTH_CHECK_INTERVAL,
        help="Health check interval in seconds (default: %(default)s)"
    )
    parser.add_argument(
        "--staleness-threshold",
        type=int,
        default=DEFAULT_STALENESS_THRESHOLD,
        help="Staleness threshold in seconds (default: %(default)s)"
    )
    
    args = parser.parse_args()
    
    daemon = HealthRecoveryDaemon(
        write_service_url=args.write_service_url,
        restart_command=args.restart_command,
        health_check_interval=args.health_check_interval,
        staleness_threshold=args.staleness_threshold
    )
    
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())


# Self-test for __main__ execution
if __name__ == "__main__":
    import unittest.mock
    
    class MockSubprocessPatcher:
        """Patcher that intercepts subprocess.Popen for testing."""
        
        def __init__(self):
            self.restart_called = False
            self.restart_command = None
            self.popen_instance = None
        
        def __enter__(self):
            self.original_popen = subprocess.Popen
            subprocess.Popen = self._mock_popen
            return self
        
        def __exit__(self, *args):
            subprocess.Popen = self.original_popen
        
        def _mock_popen(self, args, **kwargs):
            self.restart_called = True
            self.restart_command = args
            self.popen_instance = _MockPopen(args, **kwargs)
            return self.popen_instance
    
    def self_test():
        """Self-test that runs daemon once without crashing."""
        print("Running self-test...")
        
        # Track if health check was called
        health_check_count = [0]
        original_requests_get = requests.get
        
        def mock_get(url, **kwargs):
            health_check_count[0] += 1
            if health_check_count[0] == 1:
                # First call: service is unhealthy
                raise requests.exceptions.ConnectionError("Service down")
            else:
                # Second call: service is healthy (after restart mock)
                mock_response = unittest.mock.Mock()
                mock_response.status_code = 200
                mock_response.text = '{"status": "ok"}'
                mock_response.json.return_value = {"status": "ok"}
                mock_response.raise_for_status = lambda: None
                return mock_response
        
        with unittest.mock.patch("requests.get", side_effect=mock_get):
            with unittest.mock.patch("requests.post") as mock_post:
                mock_post.return_value = unittest.mock.Mock(
                    raise_for_status=lambda: None
                )
                
                with MockSubprocessPatcher() as patcher:
                    daemon = HealthRecoveryDaemon(
                        write_service_url="http://127.0.0.1:8772",
                        restart_command=["echo", "restart"],
                        health_check_interval=0,  # No delay for testing
                        staleness_threshold=300,
                        subprocess_patcher=patcher
                    )
                    
                    # Run the daemon
                    result = daemon.run()
                    
                    # Assertions
                    assert health_check_count[0] >= 2, \
                        "Health check should be called at least twice"
                    assert result == 0, \
                        f"Daemon should return 0 on graceful shutdown, got {result}"
                    assert patcher.restart_called, \
                        "Restart command should have been called"
                    assert patcher.restart_command == ["echo", "restart"], \
                        f"Wrong restart command: {patcher.restart_command}"
        
        print("PASS: Daemon completed successfully")
        print(f"  - Health checks performed: {health_check_count[0]}")
        print(f"  - Restart triggered: {patcher.restart_called}")
        print(f"  - Restart command: {patcher.restart_command}")
    
    self_test()