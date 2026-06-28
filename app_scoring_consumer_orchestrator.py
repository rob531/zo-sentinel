import time
import threading
import datetime
import logging
import collections
import sys

# --- Configuration ---
HEARTBEAT_INTERVAL_SECONDS = 30
PROCESSING_INTERVAL_SECONDS = 5

# --- Logger Setup ---
# Configure logging to output to stdout with a specific format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- Data Structures (Simulated) ---
# Using a namedtuple to represent a single app score entry,
# similar to a row in the 'mcp_llm_axis_scores' table.
AppScore = collections.namedtuple(
    "AppScore",
    ["app_id", "axis_name", "score", "timestamp"]
)

# --- Simulated Data Source ---
def _simulate_data_fetch(last_processed_timestamp: datetime.datetime) -> list[AppScore]:
    """
    Simulates fetching new app scoring data from a data source.
    In a real system, this would involve database queries, API calls, etc.,
    fetching records newer than the `last_processed_timestamp`.

    Args:
        last_processed_timestamp: The timestamp of the latest record
                                  processed in the previous cycle.

    Returns:
        A list of AppScore namedtuples representing new data.
    """
    logger.debug(f"Simulating data fetch since {last_processed_timestamp.isoformat()}")

    current_time = datetime.datetime.now(datetime.timezone.utc)
    simulated_data = []

    # Initial data that might be fetched on the first run or if timestamp is very old
    initial_batch = [
        AppScore("app_A", "performance", 0.85, current_time - datetime.timedelta(minutes=10)),
        AppScore("app_A", "security", 0.92, current_time - datetime.timedelta(minutes=10)),
        AppScore("app_B", "performance", 0.78, current_time - datetime.timedelta(minutes=12)),
        AppScore("app_B", "usability", 0.88, current_time - datetime.timedelta(minutes=12)),
        AppScore("app_C", "security", 0.95, current_time - datetime.timedelta(minutes=15)),
    ]
    # Filter initial batch to only include data newer than last_processed_timestamp
    simulated_data.extend([s for s in initial_batch if s.timestamp > last_processed_timestamp])

    # Simulate new data appearing over time to demonstrate continuous processing
    # This ensures that in subsequent cycles, new data is "discovered"
    if current_time - last_processed_timestamp > datetime.timedelta(seconds=PROCESSING_INTERVAL_SECONDS * 1.5):
        new_data_time = current_time - datetime.timedelta(seconds=PROCESSING_INTERVAL_SECONDS // 2)
        simulated_data.append(AppScore("app_A", "usability", 0.90, new_data_time))
        simulated_data.append(AppScore("app_D", "performance", 0.70, new_data_time))
        simulated_data.append(AppScore("app_D", "security", 0.80, new_data_time))
        logger.info(f"Generated new simulated data points for current cycle.")

    return simulated_data

# --- Data Processing Logic ---
def _process_data(raw_scores: list[AppScore]) -> dict:
    """
    Processes the raw app scoring data.
    For this example, it calculates the average score per app_id and axis_name.
    In a real scenario, this could involve more complex transformations,
    enrichment, or aggregation before making it available.

    Args:
        raw_scores: A list of AppScore namedtuples to process.

    Returns:
        A dictionary where keys are app_ids, and values are dictionaries
        mapping axis_names to their calculated average scores.
    """
    if not raw_scores:
        logger.info("No raw scores to process.")
        return {}

    # Use defaultdict to easily group scores by app_id and then by axis_name
    grouped_scores = collections.defaultdict(lambda: collections.defaultdict(list))
    for score_entry in raw_scores:
        grouped_scores[score_entry.app_id][score_entry.axis_name].append(score_entry.score)

    final_results = {}
    for app_id, axis_scores in grouped_scores.items():
        final_results[app_id] = {}
        for axis_name, scores in axis_scores.items():
            avg_score = sum(scores) / len(scores)
            final_results[app_id][axis_name] = round(avg_score, 3) # Round for consistent output

    logger.info(f"Processed {len(raw_scores)} raw scores into {len(final_results)} app entries.")
    return final_results

# --- Heartbeat Mechanism ---
def _heartbeat(stop_event: threading.Event, interval: int):
    """
    Periodically logs a heartbeat message to indicate the orchestrator is alive.

    Args:
        stop_event: An Event object used to signal the heartbeat thread to stop.
        interval: The interval in seconds between heartbeat messages.
    """
    thread_name = threading.current_thread().name
    logger.info(f"Heartbeat thread '{thread_name}' started. Interval: {interval}s.")
    while not stop_event.wait(interval):
        logger.info(f"Heartbeat: Orchestrator is alive. Next heartbeat in {interval} seconds.")
    logger.info(f"Heartbeat thread '{thread_name}' stopped.")

# --- Main Orchestrator Function ---
def run(stop_event: threading.Event = None):
    """
    Main function for the app scoring consumer orchestrator.
    It continuously fetches, processes, and makes app scoring data available.
    Adheres to a standard daemon pattern with a heartbeat.

    Args:
        stop_event: An optional threading.Event to signal the orchestrator to stop.
                    If not provided, the orchestrator runs indefinitely until
                    a KeyboardInterrupt or unhandled exception occurs.
    """
    if stop_event is None:
        # Create a new event if not provided, for standalone execution
        stop_event = threading.Event()

    logger.info("App Scoring Consumer Orchestrator starting...")

    # Start the heartbeat thread
    heartbeat_thread = threading.Thread(
        target=_heartbeat,
        args=(stop_event, HEARTBEAT_INTERVAL_SECONDS),
        name="HeartbeatThread"
    )
    # Set as daemon so it doesn't prevent the main program from exiting
    # if the main thread terminates unexpectedly, but we'll still join it.
    heartbeat_thread.daemon = True
    heartbeat_thread.start()

    # Initialize last_processed_timestamp to the earliest possible UTC time
    # to ensure all initial data is fetched on the first run.
    last_processed_timestamp = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

    # In-memory store for processed data, simulating availability for downstream consumers.
    # This dictionary will hold the latest aggregated scores.
    processed_data_store = {}

    try:
        while not stop_event.is_set():
            logger.info("Orchestrator cycle started: Fetching new data...")
            raw_scores = _simulate_data_fetch(last_processed_timestamp)

            if raw_scores:
                # Update last_processed_timestamp to the latest timestamp found in the current batch.
                # This ensures that in the next cycle, we only fetch data newer than this point.
                latest_timestamp_in_batch = max(s.timestamp for s in raw_scores)
                if latest_timestamp_in_batch > last_processed_timestamp:
                    last_processed_timestamp = latest_timestamp_in_batch
                    logger.debug(f"Updated last_processed_timestamp to {last_processed_timestamp.isoformat()}")

                logger.info(f"Fetched {len(raw_scores)} new raw scores.")
                current_processed_data = _process_data(raw_scores)

                # Merge the newly processed data into the main store.
                # This example overwrites existing app_id/axis_name scores with the latest.
                for app_id, axis_scores in current_processed_data.items():
                    if app_id not in processed_data_store:
                        processed_data_store[app_id] = {}
                    processed_data_store[app_id].update(axis_scores)

                logger.info(f"Current state of processed data store: {processed_data_store}")
            else:
                logger.info("No new data fetched in this cycle.")

            logger.info(f"Orchestrator cycle finished. Waiting {PROCESSING_INTERVAL_SECONDS} seconds for next cycle.")
            # Wait for the next processing interval or until stop_event is set
            stop_event.wait(PROCESSING_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt received. Signaling orchestrator shutdown.")
    except Exception as e:
        logger.exception(f"An unhandled error occurred in the orchestrator: {e}")
    finally:
        # Ensure the stop_event is set to signal all threads to terminate
        stop_event.set()
        logger.info("Waiting for heartbeat thread to terminate...")
        # Wait for the heartbeat thread to finish, with a timeout
        heartbeat_thread.join(timeout=HEARTBEAT_INTERVAL_SECONDS + 5)
        if heartbeat_thread.is_alive():
            logger.warning("Heartbeat thread did not terminate gracefully.")
        logger.info("App Scoring Consumer Orchestrator stopped.")

# --- Self-Test ---
if __name__ == "__main__":
    logger.info("--- Starting App Scoring Consumer Orchestrator Self-Test ---")

    # Set logging level to DEBUG for more detailed output during the test
    logging.getLogger().setLevel(logging.DEBUG)

    # Acceptance Criteria 1: Verify module can be imported and its main function can be called without errors.
    # We run the `run` function in a separate thread and signal it to stop after a short duration.
    test_run_duration_seconds = 15 # How long the orchestrator will run for the test
    test_stop_event = threading.Event()

    logger.info(f"Running orchestrator in a separate thread for {test_run_duration_seconds} seconds...")
    orchestrator_thread = threading.Thread(
        target=run,
        args=(test_stop_event,),
        name="OrchestratorMainTestThread"
    )
    orchestrator_thread.start()

    # Wait for the specified duration to allow multiple cycles and heartbeats
    time.sleep(test_run_duration_seconds)

    # Signal the orchestrator to stop gracefully
    logger.info("Signaling orchestrator to stop for self-test...")
    test_stop_event.set()
    # Wait for the orchestrator thread to finish, with a timeout
    orchestrator_thread.join(timeout=PROCESSING_INTERVAL_SECONDS + HEARTBEAT_INTERVAL_SECONDS + 10)

    if orchestrator_thread.is_alive():
        logger.error("Orchestrator thread did not terminate gracefully during self-test.")
        sys.exit(1)
    else:
        logger.info("Orchestrator thread terminated successfully.")
        logger.info("Self-test of orchestrator daemon pattern (import and run) PASSED.")

    # Acceptance Criteria 2: Verify it correctly processes a small, in-memory dataset.
    logger.info("Verifying processing logic with a direct call to _process_data...")
    test_raw_data = [
        AppScore("app_X", "quality", 0.90, datetime.datetime.now(datetime.timezone.utc)),
        AppScore("app_X", "quality", 0.80, datetime.datetime.now(datetime.timezone.utc)),
        AppScore("app_Y", "speed", 0.75, datetime.datetime.now(datetime.timezone.utc)),
        AppScore("app_Y", "speed", 0.85, datetime.datetime.now(datetime.timezone.utc)),
        AppScore("app_Z", "reliability", 0.99, datetime.datetime.now(datetime.timezone.utc)),
    ]
    # Expected average scores:
    # app_X, quality: (0.90 + 0.80) / 2 = 0.85
    # app_Y, speed: (0.75 + 0.85) / 2 = 0.80
    # app_Z, reliability: 0.99
    expected_processed_data = {
        "app_X": {"quality": 0.850},
        "app_Y": {"speed": 0.800},
        "app_Z": {"reliability": 0.990},
    }
    actual_processed_data = _process_data(test_raw_data)

    # Helper to round float values in a nested dictionary for comparison
    def _round_dict_values(d):
        if isinstance(d, dict):
            return {k: _round_dict_values(v) for k, v in d.items()}
        elif isinstance(d, float):
            return round(d, 3)
        return d

    actual_processed_data_rounded = _round_dict_values(actual_processed_data)
    expected_processed_data_rounded = _round_dict_values(expected_processed_data)

    if actual_processed_data_rounded == expected_processed_data_rounded:
        logger.info("Processing logic verification PASSED.")
    else:
        logger.error("Processing logic verification FAILED.")
        logger.error(f"Expected: {expected_processed_data_rounded}")
        logger.error(f"Actual: {actual_processed_data_rounded}")
        sys.exit(1)

    logger.info("--- App Scoring Consumer Orchestrator Self-Test Complete ---")
    sys.exit(0)