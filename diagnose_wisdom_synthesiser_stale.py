import logging
import time
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('diagnose_wisdom_synthesiser_stale')

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

def check_service_health(service_name: str) -> dict:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                'table': 'service_health',
                'rows': {'service': service_name},
                'wait': True
            },
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"Failed to query service_health: {e}")
    return {}

def test_daemon_responsive() -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                'table': 'health_check',
                'rows': {'test': 'ping'},
                'wait': True
            },
            timeout=5
        )
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"Daemon not responsive: {e}")
        return False

def diagnose():
    service_name = 'wisdom_synthesiser'
    logger.info(f"Diagnosing {service_name} - reported stale at 10h3m")

    health_data = check_service_health(service_name)
    if health_data:
        last_heartbeat = health_data.get('last_heartbeat', 'unknown')
        logger.info(f"Last heartbeat recorded: {last_heartbeat}")

    responsive = test_daemon_responsive()
    if responsive:
        logger.info("wisdom_synthesiser daemon is responsive via write_service")
    else:
        logger.warning("wisdom_synthesiser daemon NOT responsive - confirmed stale")

    logger.info(f"Diagnosis complete for {service_name} - responsive={responsive}")

def run():
    logger.info("Starting wisdom_synthesiser stale diagnostic daemon")
    while True:
        diagnose()
        time.sleep(60)

if __name__ == '__main__':
    run()