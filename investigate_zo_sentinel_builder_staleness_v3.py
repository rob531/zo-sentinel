#!/usr/bin/env python3
import os
import sys
import subprocess
import logging
import time
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('zo_sentinel_builder_staleness_investigation.log'),
        logging.StreamHandler()
    ]
)

def check_heartbeat_staleness():
    """Check if the heartbeat is stale by comparing the last heartbeat time with the current time."""
    heartbeat_file = '/var/run/zo_sentinel_builder/heartbeat'
    if not os.path.exists(heartbeat_file):
        logging.error(f"Heartbeat file not found: {heartbeat_file}")
        return False

    with open(heartbeat_file, 'r') as f:
        last_heartbeat = float(f.read().strip())

    current_time = time.time()
    stale_threshold = 60  # 60 seconds threshold for staleness
    if current_time - last_heartbeat > stale_threshold:
        logging.warning(f"Heartbeat is stale. Last heartbeat: {datetime.fromtimestamp(last_heartbeat)}, Current time: {datetime.fromtimestamp(current_time)}")
        return True
    else:
        logging.info("Heartbeat is fresh.")
        return False

def check_logs_for_errors():
    """Check the logs for any errors or warnings."""
    log_file = '/var/log/zo_sentinel_builder/zo_sentinel_builder.log'
    if not os.path.exists(log_file):
        logging.error(f"Log file not found: {log_file}")
        return False

    with open(log_file, 'r') as f:
        logs = f.readlines()

    errors = [log for log in logs if 'ERROR' in log]
    warnings = [log for log in logs if 'WARNING' in log]

    if errors:
        logging.error(f"Found {len(errors)} errors in the log:")
        for error in errors:
            logging.error(error.strip())
    else:
        logging.info("No errors found in the log.")

    if warnings:
        logging.warning(f"Found {len(warnings)} warnings in the log:")
        for warning in warnings:
            logging.warning(warning.strip())
    else:
        logging.info("No warnings found in the log.")

    return True

def check_process_status():
    """Check if the zo_sentinel_builder process is running."""
    process_name = 'zo_sentinel_builder'
    try:
        output = subprocess.check_output(['pgrep', '-f', process_name])
        if output:
            logging.info(f"Process {process_name} is running with PID: {output.decode().strip()}")
            return True
        else:
            logging.error(f"Process {process_name} is not running.")
            return False
    except subprocess.CalledProcessError:
        logging.error(f"Process {process_name} is not running.")
        return False

def check_for_blocking():
    """Check if the process is blocked or waiting for resources."""
    process_name = 'zo_sentinel_builder'
    try:
        output = subprocess.check_output(['ps', '-eo', 'pid,stat,cmd'], text=True)
        for line in output.splitlines():
            if process_name in line and 'D' in line.split()[1]:  # 'D' indicates uninterruptible sleep (blocked)
                logging.warning(f"Process {process_name} is blocked: {line.strip()}")
                return True
        logging.info("Process is not blocked.")
        return False
    except subprocess.CalledProcessError as e:
        logging.error(f"Error checking process status: {e}")
        return False

def main():
    logging.info("Starting investigation of zo_sentinel_builder staleness.")

    if check_heartbeat_staleness():
        logging.info("Investigating further due to stale heartbeat.")

        check_logs_for_errors()
        check_process_status()
        check_for_blocking()

        logging.info("Investigation completed. Check logs for detailed findings.")
    else:
        logging.info("No staleness detected. Exiting.")

if __name__ == "__main__":
    main()