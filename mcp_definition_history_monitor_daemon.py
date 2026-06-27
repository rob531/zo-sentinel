import time
import logging
from datetime import datetime, timedelta
from typing import Optional

# Mock dependencies (replace with actual imports in production)
class WriteService:
    def __init__(self):
        self.queries = []

    def query(self, sql: str, params: Optional[tuple] = None) -> list:
        self.queries.append((sql, params))
        return []

    def insert(self, table: str, data: dict) -> None:
        self.queries.append(("INSERT", table, data))

class ServiceHealth:
    def __init__(self):
        self.heartbeats = []

    def heartbeat(self, service_name: str) -> None:
        self.heartbeats.append((service_name, datetime.now()))

# Daemon implementation
class MCPDefinitionHistoryMonitorDaemon:
    def __init__(self, write_service: WriteService, service_health: ServiceHealth):
        self.write_service = write_service
        self.service_health = service_health
        self.logger = logging.getLogger(__name__)
        self.threshold_hours = 24  # Configurable threshold

    def _is_table_empty_or_stale(self) -> bool:
        """Check if the table is empty or stale."""
        # Query the latest entry
        sql = "SELECT MAX(created_at) FROM mcp_definition_history"
        result = self.write_service.query(sql)

        if not result or not result[0][0]:
            self.logger.info("Table is empty")
            return True

        latest_time = result[0][0]
        threshold_time = datetime.now() - timedelta(hours=self.threshold_hours)

        if latest_time < threshold_time:
            self.logger.info(f"Latest entry is older than {self.threshold_hours} hours")
            return True

        return False

    def _trigger_population(self) -> None:
        """Trigger population of the table."""
        # Option 1: Internal API call (pseudo-code)
        # response = internal_api_call("trigger_mcp_definition_history_backfill")

        # Option 2: Write to control table
        self.write_service.insert(
            "control_table",
            {"action": "trigger_mcp_definition_history_backfill", "status": "pending"}
        )
        self.logger.info("Triggered population of mcp_definition_history")

    def run(self) -> None:
        """Main daemon loop with heartbeat."""
        while True:
            try:
                self.service_health.heartbeat("mcp_definition_history_monitor")

                if self._is_table_empty_or_stale():
                    self._trigger_population()

                time.sleep(60)  # Sleep for 60 seconds

            except Exception as e:
                self.logger.error(f"Error in daemon loop: {e}")
                time.sleep(60)  # Sleep before retrying

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Mock services
    write_service = WriteService()
    service_health = ServiceHealth()

    # Simulate empty table
    daemon = MCPDefinitionHistoryMonitorDaemon(write_service, service_health)

    # Test empty table case
    logger.info("Testing empty table case...")
    daemon._is_table_empty_or_stale()  # Should return True
    daemon._trigger_population()  # Should trigger population

    # Assert that the trigger was attempted
    if any("trigger_mcp_definition_history_backfill" in q for q in write_service.queries):
        print("PASS")
    else:
        print("FAIL")