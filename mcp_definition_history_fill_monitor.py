import requests
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
WRITE_SERVICE_URL = "http://write_service_url"  # Replace with actual URL
MCP_DEFINITION_HISTORY_TABLE = "mcp_definition_history"
MIN_ROWS_THRESHOLD = 100
POPULATOR_SCRIPT = "mcp_definition_history_populator.py"
DELAY_SECONDS = 60  # Delay before re-checking row count

def get_row_count():
    """Query the row count of the mcp_definition_history table."""
    query = f"SELECT COUNT(*) FROM {MCP_DEFINITION_HISTORY_TABLE};"
    response = requests.post(WRITE_SERVICE_URL, json={"query": query})
    response.raise_for_status()
    return response.json()["count"]

def trigger_population():
    """Trigger the population of the mcp_definition_history table."""
    try:
        # Attempt to invoke the populator script directly
        import mcp_definition_history_populator
        mcp_definition_history_populator.main()
        logger.info("Successfully triggered population via direct invocation.")
    except ImportError:
        # Log directive for external mechanism if direct invocation fails
        logger.info(f"Directive: Trigger population via external mechanism using {POPULATOR_SCRIPT}")

def monitor_and_trigger():
    """Monitor the mcp_definition_history table and trigger population if necessary."""
    initial_count = get_row_count()
    logger.info(f"Initial row count in {MCP_DEFINITION_HISTORY_TABLE}: {initial_count}")

    if initial_count < MIN_ROWS_THRESHOLD:
        logger.info(f"Row count below threshold ({MIN_ROWS_THRESHOLD}). Triggering population.")
        trigger_population()

        # Wait for a reasonable delay before re-checking
        time.sleep(DELAY_SECONDS)

        final_count = get_row_count()
        logger.info(f"Final row count in {MCP_DEFINITION_HISTORY_TABLE}: {final_count}")

        if final_count > initial_count:
            logger.info("Population was successful. Row count increased.")
        else:
            logger.warning("Population may not have been successful. Row count did not increase.")
    else:
        logger.info("Row count is above threshold. No action taken.")

if __name__ == "__main__":
    monitor_and_trigger()