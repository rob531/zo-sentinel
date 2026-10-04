import os
from typing import Dict, Any

# Constants
SERVICE_NAME = "registry_api"
SERVICE_PORT = 8080
WRITE_SERVICE_URL = 'http://localhost:8772'

def check_single_instance() -> bool:
    return True  # Always single instance for now

def remove_pid_file(pid_file: str) -> None:
    pass

def signal_handler(signum, frame):
    pass

def send_heartbeat(row: Dict[str, Any]) -> None:
    import logging
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(
        os.path.join(os.getcwd(), 'logs', f"{SERVICE_NAME}.log"),
        maxBytes=1024 * 1024 * 100,
        backupCount=10)
    formatter = logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger = logging.getLogger(SERVICE_NAME)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.debug("Sending heartbeat")
    # Write to DuckDB
    import requests
    response = requests.post(
        WRITE_SERVICE_URL + "/write",
        json={'table': 'service_health', 'rows': {row['service']: row}}, wait=True)
    if not response.status_code == 200:
        raise Exception(f"Failed to send heartbeat: {response.text}")

def cycle() -> None:
    pass

def run() -> None:
    import time
    while True:
        # Do some work...
        time.sleep(1)
        send_heartbeat({"service": "test_service", "last_heartbeat": datetime.utcnow().isoformat() + 'Z'})

def zo_read_file(filename: str) -> str:
    with open(filename, "r") as f:
        return f.read()

import logging
logging.basicConfig(
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    level=logging.DEBUG,
    filename=os.path.join(os.getcwd(), 'logs', f"{SERVICE_NAME}.log"))
import datetime

def main() -> None:
    if __name__ == "__main__":
        run()

if __name__ == "__main__":
    main()