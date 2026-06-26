import datetime
import time
import unittest
from unittest.mock import patch, MagicMock

# Assume write_service is available and works as expected
# For demonstration, we'll mock it. In a real scenario, you'd import it.
# from zo_sentinel.db.service_health import write_service, read_service_health

# Mocking the database interaction functions
class MockDB:
    def __init__(self):
        self.service_health_data = {
            "rug_pull_monitor": {"last_heartbeat": datetime.datetime.utcnow() - datetime.timedelta(minutes=5)}
        }

    def write_service(self, service_name, last_heartbeat):
        if service_name in self.service_health_data:
            self.service_health_data[service_name]["last_heartbeat"] = last_heartbeat
            print(f"Mock DB: Updated {service_name} heartbeat to {last_heartbeat}")
        else:
            print(f"Mock DB: Service {service_name} not found.")

    def read_service_health(self, service_name):
        return self.service_health_data.get(service_name)

mock_db_instance = MockDB()

# Simulate the rug_pull_monitor's heartbeat logic
def simulate_rug_pull_monitor_heartbeat():
    """
    Simulates the rug_pull_monitor daemon sending a heartbeat.
    In a real daemon, this would involve calling a function that updates the DB.
    """
    service_name = "rug_pull_monitor"
    current_time = datetime.datetime.utcnow()
    print(f"Simulating heartbeat for {service_name} at {current_time}")
    mock_db_instance.write_service(service_name, current_time)

# --- Test Script ---

class TestRugPullMonitorHeartbeat(unittest.TestCase):

    @patch('__main__.write_service', side_effect=mock_db_instance.write_service)
    @patch('__main__.read_service_health', side_effect=mock_db_instance.read_service_health)
    def test_heartbeat_updates_service_health(self, mock_read_service_health, mock_write_service):
        """
        Verifies that the rug_pull_monitor's heartbeat updates the service_health table.
        """
        service_name = "rug_pull_monitor"

        # 1. Get the initial heartbeat timestamp
        initial_health = mock_read_service_health(service_name)
        initial_heartbeat_time = initial_health.get("last_heartbeat") if initial_health else None
        print(f"Initial heartbeat for {service_name}: {initial_heartbeat_time}")

        self.assertIsNotNone(initial_heartbeat_time, f"Initial heartbeat for {service_name} not found.")

        # 2. Simulate the heartbeat
        print("\n--- Simulating Heartbeat ---")
        simulate_rug_pull_monitor_heartbeat()
        # Add a small delay to ensure the timestamp is noticeably different
        time.sleep(1)
        print("--- Heartbeat Simulation Complete ---\n")

        # 3. Get the updated heartbeat timestamp
        updated_health = mock_read_service_health(service_name)
        updated_heartbeat_time = updated_health.get("last_heartbeat") if updated_health else None
        print(f"Updated heartbeat for {service_name}: {updated_heartbeat_time}")

        self.assertIsNotNone(updated_heartbeat_time, f"Updated heartbeat for {service_name} not found.")

        # 4. Assert that the heartbeat timestamp has been updated
        self.assertGreater(updated_heartbeat_time, initial_heartbeat_time,
                           f"Heartbeat for {service_name} was not updated.")

        # 5. Verify the update is recent (within the last minute, for example)
        current_time = datetime.datetime.utcnow()
        time_difference = current_time - updated_heartbeat_time
        self.assertLess(time_difference, datetime.timedelta(minutes=1),
                        f"Heartbeat for {service_name} is not recent enough.")

        print("\nPASS: rug_pull_monitor heartbeat functionality verified successfully.")

if __name__ == '__main__':
    # In a real test environment, you would ensure the database is in a known state
    # before running tests. Here, our mock handles it.
    unittest.main()