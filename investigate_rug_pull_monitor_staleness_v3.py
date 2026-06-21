import os
import subprocess
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('rug_pull_monitor_staleness_investigation.log'),
        logging.StreamHandler()
    ]
)

def check_daemon_heartbeat():
    """Check the last heartbeat of the rug_pull_monitor daemon."""
    try:
        # Assuming the heartbeat is stored in a file
        heartbeat_file = '/var/run/rug_pull_monitor/heartbeat'
        if not os.path.exists(heartbeat_file):
            logging.error(f"Heartbeat file not found: {heartbeat_file}")
            return False

        with open(heartbeat_file, 'r') as f:
            last_heartbeat = float(f.read().strip())

        current_time = datetime.now().timestamp()
        stale_threshold = 60  # 60 seconds threshold for staleness

        if current_time - last_heartbeat > stale_threshold:
            logging.warning(f"Daemon heartbeat is stale. Last heartbeat: {datetime.fromtimestamp(last_heartbeat)}")
            return False
        else:
            logging.info(f"Daemon heartbeat is recent. Last heartbeat: {datetime.fromtimestamp(last_heartbeat)}")
            return True
    except Exception as e:
        logging.error(f"Error checking heartbeat: {e}")
        return False

def check_daemon_logs():
    """Check the logs of the rug_pull_monitor daemon for errors."""
    try:
        log_file = '/var/log/rug_pull_monitor/rug_pull_monitor.log'
        if not os.path.exists(log_file):
            logging.error(f"Log file not found: {log_file}")
            return False

        with open(log_file, 'r') as f:
            logs = f.readlines()

        error_lines = [line.strip() for line in logs if 'ERROR' in line or 'CRITICAL' in line]

        if error_lines:
            logging.error(f"Found errors in daemon logs: {error_lines}")
            return False
        else:
            logging.info("No errors found in daemon logs.")
            return True
    except Exception as e:
        logging.error(f"Error checking logs: {e}")
        return False

def check_daemon_status():
    """Check the status of the rug_pull_monitor daemon."""
    try:
        result = subprocess.run(['systemctl', 'is-active', 'rug_pull_monitor'], capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"Daemon is not active: {result.stderr}")
            return False
        else:
            logging.info("Daemon is active.")
            return True
    except Exception as e:
        logging.error(f"Error checking daemon status: {e}")
        return False

def restart_daemon():
    """Restart the rug_pull_monitor daemon."""
    try:
        logging.info("Attempting to restart the daemon...")
        subprocess.run(['systemctl', 'restart', 'rug_pull_monitor'], check=True)
        logging.info("Daemon restarted successfully.")
        return True
    except Exception as e:
        logging.error(f"Error restarting daemon: {e}")
        return False

def main():
    logging.info("Starting investigation of rug_pull_monitor daemon staleness...")

    heartbeat_ok = check_daemon_heartbeat()
    logs_ok = check_daemon_logs()
    status_ok = check_daemon_status()

    if not heartbeat_ok or not logs_ok or not status_ok:
        logging.warning("Issues detected. Attempting to restart the daemon...")
        if not restart_daemon():
            logging.error("Failed to restart the daemon. Manual intervention may be required.")
    else:
        logging.info("Daemon is functioning correctly.")

    logging.info("Investigation completed.")

if __name__ == "__main__":
    main()