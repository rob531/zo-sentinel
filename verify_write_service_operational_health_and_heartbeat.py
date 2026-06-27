import requests
import logging
import sys
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_write_service_health(write_service_url, query_endpoint_url):
    """
    Verify the operational health and heartbeat of the write_service.

    Args:
        write_service_url (str): URL of the write_service health endpoint.
        query_endpoint_url (str): URL of the query endpoint to check service_health.

    Returns:
        bool: True if the write_service is healthy, False otherwise.
    """
    try:
        # Check write_service health endpoint if available
        if write_service_url:
            logger.info(f"Checking write_service health endpoint: {write_service_url}")
            response = requests.get(write_service_url)
            if response.status_code != 200:
                logger.error(f"write_service health endpoint returned status code: {response.status_code}")
                return False

        # Query service_health table for last_heartbeat
        logger.info(f"Querying service_health table via {query_endpoint_url}")
        query = """
        SELECT last_heartbeat
        FROM service_health
        WHERE service_name = 'write_service'
        """
        payload = {'query': query}
        response = requests.get(query_endpoint_url, params=payload)
        if response.status_code != 200:
            logger.error(f"Query endpoint returned status code: {response.status_code}")
            return False

        data = response.json()
        if not data or 'results' not in data or not data['results']:
            logger.error("No results returned from query")
            return False

        last_heartbeat = data['results'][0]['last_heartbeat']
        logger.info(f"Last heartbeat: {last_heartbeat}")

        # Check if heartbeat is older than 5 minutes
        last_heartbeat_time = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
        five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
        if last_heartbeat_time < five_minutes_ago:
            logger.error("Last heartbeat is older than 5 minutes")
            return False

        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return False
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return False

if __name__ == "__main__":
    # Configuration
    WRITE_SERVICE_URL = "http://write_service:8080/health"  # Set to None if not available
    QUERY_ENDPOINT_URL = "http://write_service:8080/query"

    # Check write_service health
    is_healthy = check_write_service_health(WRITE_SERVICE_URL, QUERY_ENDPOINT_URL)

    # Print result and exit with appropriate status code
    if is_healthy:
        print("PASS - write_service is healthy")
        sys.exit(0)
    else:
        print("FAIL - write_service is not healthy")
        sys.exit(1)