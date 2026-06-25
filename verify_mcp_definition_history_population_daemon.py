import logging
import time
import requests
from threading import Thread
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPDefinitionHistoryDaemon:
    def __init__(self, interval=60, min_rows=1, db_url="http://write_service/db/query"):
        self.interval = interval
        self.min_rows = min_rows
        self.db_url = db_url
        self.last_check = datetime.now()
        self.last_row_count = 0
        self.health_url = "http://service_health/heartbeat"
        self.running = False

    def query_db(self):
        """Query the mcp_definition_history table for row count."""
        try:
            response = requests.post(
                self.db_url,
                json={
                    "query": "SELECT COUNT(*) FROM mcp_definition_history",
                    "timeout": 10
                },
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()
            return response.json()["count"]
        except requests.RequestException as e:
            logger.error(f"Database query failed: {e}")
            return 0

    def check_health(self):
        """Check if the table is being populated correctly."""
        current_count = self.query_db()
        if current_count == 0:
            logger.warning("mcp_definition_history table is empty!")
            return False
        if current_count <= self.last_row_count:
            logger.warning("mcp_definition_history table has not grown since last check.")
            return False
        self.last_row_count = current_count
        return True

    def send_heartbeat(self):
        """Send a heartbeat to service_health."""
        try:
            requests.post(
                self.health_url,
                json={"status": "healthy"},
                headers={"Content-Type": "application/json"},
                timeout=5
            )
        except requests.RequestException as e:
            logger.error(f"Heartbeat failed: {e}")

    def run(self):
        """Main daemon loop."""
        self.running = True
        logger.info("Starting MCP Definition History Daemon")
        while self.running:
            self.send_heartbeat()
            if not self.check_health():
                logger.warning("mcp_definition_history table is not healthy.")
            time.sleep(self.interval)

    def stop(self):
        """Stop the daemon."""
        self.running = False

def run():
    """Run the daemon."""
    daemon = MCPDefinitionHistoryDaemon()
    try:
        daemon.run()
    except KeyboardInterrupt:
        logger.info("Stopping daemon...")
        daemon.stop()

if __name__ == '__main__':
    # Test simulation
    import unittest
    from unittest.mock import patch, MagicMock

    class TestMCPDefinitionHistoryDaemon(unittest.TestCase):
        @patch('requests.post')
        def test_healthy_state(self, mock_post):
            # Simulate a healthy database with growing rows
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"count": 5}
            )
            daemon = MCPDefinitionHistoryDaemon(min_rows=1)
            self.assertTrue(daemon.check_health())

            # Simulate growth
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"count": 10}
            )
            self.assertTrue(daemon.check_health())

        @patch('requests.post')
        def test_unhealthy_state(self, mock_post):
            # Simulate an empty table
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"count": 0}
            )
            daemon = MCPDefinitionHistoryDaemon(min_rows=1)
            with self.assertLogs(logger, level='WARNING') as log:
                self.assertFalse(daemon.check_health())
                self.assertIn("mcp_definition_history table is empty!", log.output[0])

            # Simulate no growth
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"count": 5}
            )
            daemon.last_row_count = 5
            with self.assertLogs(logger, level='WARNING') as log:
                self.assertFalse(daemon.check_health())
                self.assertIn("mcp_definition_history table has not grown since last check.", log.output[0])

        @patch('requests.post')
        def test_heartbeat(self, mock_post):
            # Test heartbeat
            mock_post.return_value = MagicMock(status_code=200)
            daemon = MCPDefinitionHistoryDaemon()
            daemon.send_heartbeat()
            mock_post.assert_called_once_with(
                "http://service_health/heartbeat",
                json={"status": "healthy"},
                headers={"Content-Type": "application/json"},
                timeout=5
            )

    unittest.main(argv=[''], exit=False)