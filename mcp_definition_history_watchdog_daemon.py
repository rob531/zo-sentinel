import time
import threading
import datetime
from collections import defaultdict

# --- Mock write_service for demonstration ---
# In a real scenario, this would be an actual service client
# interacting with the database.
class MockWriteService:
    """
    A mock service to simulate database interactions and external triggers.
    All DB access is routed through this service.
    """
    def __init__(self):
        self.db_data = defaultdict(list)
        self.service_health_updates = {}
        self.alerts = []
        self.triggered_populator = False
        self.log_messages = []

        # Initialize populator health as healthy by default for testing
        # Will be overridden in __main__ for stale simulation
        self.update_service_health(
            "mcp_definition_history_populator_daemon",
            "healthy",
            details={"last_heartbeat": time.time()}
        )

    def query_db(self, table_name, conditions=None, order_by=None, limit=None):
        """Simulates querying a database table."""
        self.log_messages.append(f"QUERY_DB: {table_name} with conditions {conditions}")
        if table_name == "mcp_definition_history":
            # Simulate filtering by 'created_at' for recent entries
            if conditions and 'created_at_gte' in conditions:
                min_time = conditions['created_at_gte']
                return [
                    entry for entry in self.db_data[table_name]
                    if entry.get('created_at', 0) >= min_time
                ]
            return self.db_data[table_name]
        elif table_name == "service_health":
            if conditions and 'service_name' in conditions:
                service_name = conditions['service_name']
                return [self.service_health_updates.get(service_name)] if self.service_health_updates.get(service_name) else []
            return list(self.service_health_updates.values())
        return []

    def update_service_health(self, service_name, status, details=None):
        """Simulates updating the service_health table."""
        current_time = time.time()
        health_entry = {
            "service_name": service_name,
            "status": status,
            "last_heartbeat": current_time,
            "details": details if details is not None else {}
        }
        self.service_health_updates[service_name] = health_entry
        self.log_messages.append(f"UPDATE_SERVICE_HEALTH: {service_name} - {status} at {current_time}")

    def log_alert(self, service_name, message, severity='ERROR'):
        """Simulates logging an alert."""
        alert_entry = {
            "timestamp": time.time(),
            "service_name": service_name,
            "severity": severity,
            "message": message
        }
        self.alerts.append(alert_entry)
        self.log_messages.append(f"LOG_ALERT: {service_name} [{severity}] - {message}")

    def trigger_mcp_definition_history_populator(self):
        """Simulates calling an API to trigger the populator daemon."""
        self.triggered_populator = True
        self.log_messages.append("TRIGGERED: mcp_definition_history_populator_daemon via API")

    def add_mcp_definition_history_entry(self, timestamp):
        """Helper to add an entry to the mock mcp_definition_history table."""
        self.db_data["mcp_definition_history"].append({
            "id": len(self.db_data["mcp_definition_history"]) + 1,
            "definition_id": f"def_{len(self.db_data['mcp_definition_history']) + 1}",
            "created_at": timestamp
        })
        self.log_messages.append(f"ADDED_DB_ENTRY: mcp_definition_history at {timestamp}")

# --- Daemon Implementation ---

class McpDefinitionHistoryWatchdogDaemon:
    """
    A daemon that monitors the mcp_definition_history table and its populator
    daemon to ensure continuous and successful data population.
    """

    WATCHDOG_SERVICE_NAME = "mcp_definition_history_watchdog_daemon"
    POPULATOR_SERVICE_NAME = "mcp_definition_history_populator_daemon"

    # Configuration parameters
    WATCHDOG_INTERVAL_SECONDS = 10  # How often the watchdog performs its checks
    HEARTBEAT_INTERVAL_SECONDS = 30 # How often the watchdog emits its own heartbeat (<=60s)
    STALE_THRESHOLD_SECONDS = 120   # Populator is considered stale if no heartbeat in this duration
    RECENT_ENTRY_WINDOW_SECONDS = 300 # Look for entries in mcp_definition_history within this window (5 minutes)
    MAX_CONSECUTIVE_EMPTY_CHECKS = 3 # How many consecutive checks must show an empty table before triggering

    def __init__(self, write_service: MockWriteService):
        self.write_service = write_service
        self.last_heartbeat_time = 0
        self.consecutive_empty_checks = 0
        self._stop_event = threading.Event()
        self.daemon_thread = None

    def _get_populator_health(self):
        """Queries service_health for the populator's status."""
        health_records = self.write_service.query_db(
            "service_health",
            conditions={"service_name": self.POPULATOR_SERVICE_NAME}
        )
        if not health_records:
            self.write_service.log_alert(
                self.WATCHDOG_SERVICE_NAME,
                f"No health record found for {self.POPULATOR_SERVICE_NAME}.",
                severity="WARNING"
            )
            return None, True # Assume stale if no record

        populator_health = health_records[0]
        last_heartbeat = populator_health.get("last_heartbeat", 0)
        status = populator_health.get("status", "unknown")

        is_stale = (time.time() - last_heartbeat) > self.STALE_THRESHOLD_SECONDS
        is_unhealthy = (status != "healthy")

        return populator_health, (is_stale or is_unhealthy)

    def _check_mcp_definition_history(self):
        """Checks mcp_definition_history for recent entries."""
        time_threshold = time.time() - self.RECENT_ENTRY_WINDOW_SECONDS
        recent_entries = self.write_service.query_db(
            "mcp_definition_history",
            conditions={"created_at_gte": time_threshold}
        )
        return bool(recent_entries)

    def _emit_heartbeat(self):
        """Emits a heartbeat to the service_health table."""
        current_time = time.time()
        if (current_time - self.last_heartbeat_time) >= self.HEARTBEAT_INTERVAL_SECONDS:
            self.write_service.update_service_health(
                self.WATCHDOG_SERVICE_NAME,
                "healthy",
                details={"last_check_time": current_time}
            )
            self.last_heartbeat_time = current_time

    def _trigger_populator_or_alert(self, reason: str):
        """Attempts to trigger the populator or logs an alert."""
        alert_message = (
            f"MCP Definition History Populator requires attention. "
            f"Reason: {reason}. Attempting to trigger populator."
        )
        self.write_service.log_alert(self.WATCHDOG_SERVICE_NAME, alert_message)
        self.write_service.trigger_mcp_definition_history_populator()

    def _perform_check(self):
        """Performs a single cycle of monitoring and decision making."""
        self.write_service.log_messages.append(f"--- Watchdog Check Cycle Start ({datetime.datetime.now()}) ---")

        populator_health, populator_is_unhealthy_or_stale = self._get_populator_health()
        has_recent_entries = self._check_mcp_definition_history()

        trigger_needed = False
        reason = []

        if populator_is_unhealthy_or_stale:
            trigger_needed = True
            populator_status = populator_health.get('status', 'unknown') if populator_health else 'no_record'
            last_hb_time = populator_health.get('last_heartbeat', 0) if populator_health else 0
            stale_duration = time.time() - last_hb_time
            reason.append(f"Populator is unhealthy (status: {populator_status}) or stale (last heartbeat {stale_duration:.1f}s ago).")

        if not has_recent_entries:
            self.consecutive_empty_checks += 1
            self.write_service.log_messages.append(
                f"MCP Definition History table has no recent entries. Consecutive empty checks: {self.consecutive_empty_checks}/{self.MAX_CONSECUTIVE_EMPTY_CHECKS}"
            )
            if self.consecutive_empty_checks >= self.MAX_CONSECUTIVE_EMPTY_CHECKS:
                trigger_needed = True
                reason.append(f"MCP Definition History table consistently empty for {self.consecutive_empty_checks} checks.")
        else:
            self.consecutive_empty_checks = 0 # Reset if entries are found

        if trigger_needed:
            self._trigger_populator_or_alert("; ".join(reason))
            self.consecutive_empty_checks = 0 # Reset after triggering

        self.write_service.log_messages.append(f"--- Watchdog Check Cycle End ---")

    def run(self):
        """Starts the daemon's main loop."""
        self.write_service.log_messages.append(f"Starting {self.WATCHDOG_SERVICE_NAME}...")
        self.last_heartbeat_time = time.time() # Initialize heartbeat
        self._emit_heartbeat() # Emit initial heartbeat

        while not self._stop_event.is_set():
            try:
                self._perform_check()
                self._emit_heartbeat()
            except Exception as e:
                self.write_service.log_alert(
                    self.WATCHDOG_SERVICE_NAME,
                    f"An error occurred during watchdog operation: {e}",
                    severity="CRITICAL"
                )
            self.write_service.log_messages.append(f"Sleeping for {self.WATCHDOG_INTERVAL_SECONDS}s...")
            self._stop_event.wait(self.WATCHDOG_INTERVAL_SECONDS)
        self.write_service.log_messages.append(f"{self.WATCHDOG_SERVICE_NAME} stopped.")

    def start(self):
        """Starts the daemon in a separate thread."""
        if self.daemon_thread is None or not self.daemon_thread.is_alive():
            self._stop_event.clear()
            self.daemon_thread = threading.Thread(target=self.run, name=self.WATCHDOG_SERVICE_NAME)
            self.daemon_thread.daemon = True # Allow main program to exit even if thread is running
            self.daemon_thread.start()
            self.write_service.log_messages.append(f"{self.WATCHDOG_SERVICE_NAME} thread started.")

    def stop(self):
        """Stops the daemon."""
        self.write_service.log_messages.append(f"Stopping {self.WATCHDOG_SERVICE_NAME}...")
        self._stop_event.set()
        if self.daemon_thread:
            self.daemon_thread.join(timeout=self.WATCHDOG_INTERVAL_SECONDS + 5) # Wait for thread to finish
            if self.daemon_thread.is_alive():
                self.write_service.log_alert(
                    self.WATCHDOG_SERVICE_NAME,
                    "Daemon thread did not terminate gracefully.",
                    severity="WARNING"
                )
        self.write_service.log_messages.append(f"{self.WATCHDOG_SERVICE_NAME} stopped successfully.")


# --- Acceptance Criteria __main__ block ---
if __name__ == "__main__":
    print("--- Starting MCP Definition History Watchdog Daemon Acceptance Test ---")

    mock_write_service = MockWriteService()
    watchdog = McpDefinitionHistoryWatchdogDaemon(mock_write_service)

    # 1. Simulate a stale populator
    # Set populator's last heartbeat to be well beyond the STALE_THRESHOLD_SECONDS
    stale_time = time.time() - (watchdog.STALE_THRESHOLD_SECONDS + 60)
    mock_write_service.update_service_health(
        watchdog.POPULATOR_SERVICE_NAME,
        "healthy", # Status can be healthy, but if heartbeat is old, it's stale
        details={"last_heartbeat": stale_time}
    )
    print(f"Simulated populator last heartbeat at: {datetime.datetime.fromtimestamp(stale_time)}")

    # 2. Simulate an empty mcp_definition_history table
    # By default, mock_write_service.db_data["mcp_definition_history"] is empty.
    # No action needed here, just ensure no entries are added.
    print("Simulated mcp_definition_history table as empty.")

    # Start the watchdog daemon in a separate thread
    watchdog.start()

    # Allow the daemon to run for a few cycles to trigger the conditions
    # We need at least MAX_CONSECUTIVE_EMPTY_CHECKS + 1 cycles for the empty table condition
    # and one cycle for the stale populator condition.
    # Let's run for enough time to ensure both conditions are met and acted upon.
    # (MAX_CONSECUTIVE_EMPTY_CHECKS + 1) * WATCHDOG_INTERVAL_SECONDS
    run_duration = (watchdog.MAX_CONSECUTIVE_EMPTY_CHECKS + 2) * watchdog.WATCHDOG_INTERVAL_SECONDS
    print(f"Allowing watchdog to run for {run_duration} seconds to perform checks...")
    time.sleep(run_duration)

    # Stop the watchdog daemon
    watchdog.stop()

    # 3. Assert that the watchdog attempts to trigger the populator or logs an alert
    print("\n--- Assertions ---")

    # Check if populator was triggered
    assert mock_write_service.triggered_populator, "FAIL: Populator was NOT triggered."
    print("PASS: Populator was triggered.")

    # Check if an alert was logged
    alert_found = False
    for alert in mock_write_service.alerts:
        if watchdog.WATCHDOG_SERVICE_NAME in alert['service_name'] and \
           "Populator requires attention" in alert['message']:
            alert_found = True
            break
    assert alert_found, "FAIL: No relevant alert was logged."
    print("PASS: Relevant alert was logged.")

    # Check for watchdog heartbeats
    watchdog_health = mock_write_service.service_health_updates.get(watchdog.WATCHDOG_SERVICE_NAME)
    assert watchdog_health is not None, "FAIL: Watchdog did not emit heartbeats."
    assert (time.time() - watchdog_health['last_heartbeat']) <= (watchdog.HEARTBEAT_INTERVAL_SECONDS + watchdog.WATCHDOG_INTERVAL_SECONDS + 5), \
           f"FAIL: Watchdog heartbeat is stale. Last: {watchdog_health['last_heartbeat']}"
    print("PASS: Watchdog emitted heartbeats.")

    print("\n--- All Acceptance Criteria Met ---")
    print("PASS")

    # Optional: Print all logs for debugging
    # print("\n--- Mock Write Service Logs ---")
    # for log in mock_write_service.log_messages:
    #     print(log)