import requests
import time
import datetime
import json
import queue
import threading
import unittest.mock

# --- Configuration ---
WRITE_SERVICE_BASE_URL = "http://127.0.0.1:8772"
WRITE_SERVICE_ENDPOINT = f"{WRITE_SERVICE_BASE_URL}/write"
HEARTBEAT_SERVICE_ENDPOINT = f"{WRITE_SERVICE_BASE_URL}/service_health"

HEARTBEAT_INTERVAL_SECONDS = 30  # Must be <= 60s
POLLING_INTERVAL_SECONDS = 1     # How often to check the decision queue

class MCPDecisionLoggerDaemon:
    """
    Daemon responsible for logging analyst decisions into the `mcp_decisions` table.
    It polls an internal queue for new decisions and uses a write service to persist them.
    Includes a heartbeat mechanism.
    """

    def __init__(self, decision_queue: queue.Queue):
        """
        Initializes the daemon.

        Args:
            decision_queue: An in-memory queue from which to read analyst decisions.
        """
        self.decision_queue = decision_queue
        self._running = False
        self._last_heartbeat_time = time.monotonic()
        print(f"Daemon initialized. Write service: {WRITE_SERVICE_ENDPOINT}, Heartbeat service: {HEARTBEAT_SERVICE_ENDPOINT}")

    def _send_heartbeat(self):
        """
        Sends a heartbeat signal to the service health endpoint.
        """
        try:
            payload = {
                "service_name": "mcp_decision_logger_daemon",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            response = requests.post(HEARTBEAT_SERVICE_ENDPOINT, json=payload, timeout=5)
            response.raise_for_status()
            print(f"[{datetime.datetime.now().isoformat()}] Heartbeat sent successfully. Status: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"[{datetime.datetime.now().isoformat()}] ERROR: Failed to send heartbeat: {e}")
        finally:
            self._last_heartbeat_time = time.monotonic()

    def _process_decision(self, decision: dict):
        """
        Processes a single decision by sending it to the write service.

        Args:
            decision: A dictionary containing the analyst decision data.
        """
        print(f"[{datetime.datetime.now().isoformat()}] Processing decision for mcp_id: {decision.get('mcp_id')}")

        # Construct the data payload for the mcp_decisions table
        data_payload = {
            "mcp_id": decision.get("mcp_id"),
            "decision_type": decision.get("decision_type"),
            "analyst_id": decision.get("analyst_id"),
            "decision_timestamp": decision.get("decision_timestamp"),
        }

        # Add optional fields if they exist
        if "expiry_date" in decision and decision["expiry_date"] is not None:
            data_payload["expiry_date"] = decision["expiry_date"]
        if "conditions" in decision and decision["conditions"] is not None:
            # Ensure conditions is a JSON string
            if isinstance(decision["conditions"], dict):
                data_payload["conditions"] = json.dumps(decision["conditions"])
            else:
                data_payload["conditions"] = decision["conditions"]

        write_payload = {
            "table_name": "mcp_decisions",
            "data": data_payload
        }

        try:
            response = requests.post(WRITE_SERVICE_ENDPOINT, json=write_payload, timeout=10)
            response.raise_for_status()
            print(f"[{datetime.datetime.now().isoformat()}] Decision for mcp_id {decision.get('mcp_id')} logged successfully. Status: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"[{datetime.datetime.now().isoformat()}] ERROR: Failed to log decision for mcp_id {decision.get('mcp_id')}: {e}")

    def run(self):
        """
        Starts the daemon's main loop. It continuously polls the decision queue
        and sends heartbeats.
        """
        self._running = True
        print(f"[{datetime.datetime.now().isoformat()}] Daemon started.")
        self._send_heartbeat() # Send initial heartbeat

        while self._running:
            try:
                # Check for new decisions
                decision = self.decision_queue.get(block=False)
                self._process_decision(decision)
            except queue.Empty:
                # No decisions in the queue, continue to heartbeat check or sleep
                pass
            except Exception as e:
                print(f"[{datetime.datetime.now().isoformat()}] UNEXPECTED ERROR processing decision: {e}")

            # Check if it's time to send a heartbeat
            if time.monotonic() - self._last_heartbeat_time >= HEARTBEAT_INTERVAL_SECONDS:
                self._send_heartbeat()

            time.sleep(POLLING_INTERVAL_SECONDS)

        print(f"[{datetime.datetime.now().isoformat()}] Daemon stopped.")

    def stop(self):
        """
        Stops the daemon's main loop.
        """
        self._running = False

if __name__ == '__main__':
    print("--- Starting MCP Decision Logger Daemon Self-Test ---")

    # Mock the requests.post method to prevent actual HTTP calls
    # and to capture arguments for assertions.
    with unittest.mock.patch('requests.post') as mock_post:
        # Configure the mock to return a successful response
        mock_response = unittest.mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'status': 'success'}
        mock_post.return_value = mock_response

        # 1. Initialize the mock decision queue
        mock_decision_queue = queue.Queue()

        # 2. Seed the mock decision queue with at least 3 decisions
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        decisions_to_seed = [
            {
                "mcp_id": 101,
                "decision_type": "APPROVED",
                "analyst_id": "analyst_A",
                "decision_timestamp": now_utc.isoformat(),
                "expiry_date": (now_utc + datetime.timedelta(days=365)).isoformat(),
                "conditions": {"reason": "all checks passed", "level": "high"}
            },
            {
                "mcp_id": 102,
                "decision_type": "CONDITIONAL",
                "analyst_id": "analyst_B",
                "decision_timestamp": (now_utc - datetime.timedelta(minutes=5)).isoformat(),
                "conditions": {"reason": "pending document upload", "deadline": "2023-11-30"}
            },
            {
                "mcp_id": 103,
                "decision_type": "REJECTED",
                "analyst_id": "analyst_C",
                "decision_timestamp": (now_utc - datetime.timedelta(minutes=10)).isoformat(),
                "expiry_date": None, # Explicitly test None
                "conditions": None    # Explicitly test None
            },
            {
                "mcp_id": 104,
                "decision_type": "APPROVED",
                "analyst_id": "analyst_D",
                "decision_timestamp": (now_utc - datetime.timedelta(minutes=1)).isoformat(),
            }
        ]

        for decision in decisions_to_seed:
            mock_decision_queue.put(decision)
        print(f"Seeded {len(decisions_to_seed)} decisions into the queue.")

        # 3. Instantiate and run the daemon in a separate thread
        daemon = MCPDecisionLoggerDaemon(mock_decision_queue)
        daemon_thread = threading.Thread(target=daemon.run)
        daemon_thread.start()

        # 4. Run the daemon for a short period
        # This duration should be long enough for all decisions to be processed
        # and at least one heartbeat to be sent.
        # (len(decisions_to_seed) * POLLING_INTERVAL_SECONDS) + HEARTBEAT_INTERVAL_SECONDS + a buffer
        run_duration = (len(decisions_to_seed) * POLLING_INTERVAL_SECONDS) + HEARTBEAT_INTERVAL_SECONDS + 2
        print(f"Running daemon for {run_duration} seconds to allow processing and heartbeats...")
        time.sleep(run_duration)

        # 5. Stop the daemon and wait for its thread to finish
        daemon.stop()
        daemon_thread.join(timeout=5) # Give it a moment to shut down

        # 6. Assertions
        print("\n--- Performing Assertions ---")

        # Filter calls to /write and /service_health
        write_calls = [
            call for call in mock_post.call_args_list
            if call.args[0] == WRITE_SERVICE_ENDPOINT
        ]
        heartbeat_calls = [
            call for call in mock_post.call_args_list
            if call.args[0] == HEARTBEAT_SERVICE_ENDPOINT
        ]

        # Assert that the expected number of decisions were processed
        expected_write_calls = len(decisions_to_seed)
        assert len(write_calls) == expected_write_calls, \
            f"Expected {expected_write_calls} write calls, but got {len(write_calls)}"
        print(f"Assertion PASSED: {len(write_calls)} write calls made (expected {expected_write_calls}).")

        # Assert that at least one heartbeat was sent
        assert len(heartbeat_calls) >= 1, \
            f"Expected at least 1 heartbeat call, but got {len(heartbeat_calls)}"
        print(f"Assertion PASSED: {len(heartbeat_calls)} heartbeat calls made (expected >= 1).")

        # Verify content of write calls
        for i, expected_decision in enumerate(decisions_to_seed):
            call_args, call_kwargs = write_calls[i]
            actual_payload = call_kwargs['json']
            assert actual_payload['table_name'] == 'mcp_decisions', \
                f"Call {i}: Expected table_name 'mcp_decisions', got {actual_payload['table_name']}"

            actual_data = actual_payload['data']
            assert actual_data['mcp_id'] == expected_decision['mcp_id'], \
                f"Call {i}: Expected mcp_id {expected_decision['mcp_id']}, got {actual_data['mcp_id']}"
            assert actual_data['decision_type'] == expected_decision['decision_type'], \
                f"Call {i}: Expected decision_type {expected_decision['decision_type']}, got {actual_data['decision_type']}"
            assert actual_data['analyst_id'] == expected_decision['analyst_id'], \
                f"Call {i}: Expected analyst_id {expected_decision['analyst_id']}, got {actual_data['analyst_id']}"
            assert actual_data['decision_timestamp'] == expected_decision['decision_timestamp'], \
                f"Call {i}: Expected decision_timestamp {expected_decision['decision_timestamp']}, got {actual_data['decision_timestamp']}"

            # Check optional fields
            if expected_decision.get('expiry_date') is not None:
                assert actual_data['expiry_date'] == expected_decision['expiry_date'], \
                    f"Call {i}: Expected expiry_date {expected_decision['expiry_date']}, got {actual_data.get('expiry_date')}"
            else:
                assert 'expiry_date' not in actual_data, \
                    f"Call {i}: Expected no expiry_date, but found {actual_data.get('expiry_date')}"

            if expected_decision.get('conditions') is not None:
                expected_conditions_json = json.dumps(expected_decision['conditions']) if isinstance(expected_decision['conditions'], dict) else expected_decision['conditions']
                assert actual_data['conditions'] == expected_conditions_json, \
                    f"Call {i}: Expected conditions {expected_conditions_json}, got {actual_data.get('conditions')}"
            else:
                assert 'conditions' not in actual_data, \
                    f"Call {i}: Expected no conditions, but found {actual_data.get('conditions')}"

        print(f"Assertion PASSED: Content of all {len(write_calls)} write calls verified.")

        print("\n--- All Self-Test Assertions Passed ---")
        print('PASS')