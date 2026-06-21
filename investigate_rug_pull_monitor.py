import os
import subprocess
import time
from datetime import datetime, timedelta

def check_daemon_status():
    """Check if the rug_pull_monitor daemon is running."""
    try:
        output = subprocess.check_output(['systemctl', 'is-active', 'rug_pull_monitor.service'])
        return output.decode('utf-8').strip() == 'active'
    except subprocess.CalledProcessError:
        return False

def get_last_run_time():
    """Get the last run time of the rug_pull_monitor daemon."""
    try:
        output = subprocess.check_output(['systemctl', 'show', 'rug_pull_monitor.service', '--property=ActiveEnterTimestamp'])
        timestamp_str = output.decode('utf-8').split('=')[1].strip()
        return datetime.fromtimestamp(float(timestamp_str))
    except subprocess.CalledProcessError:
        return None

def investigate_staleness():
    """Investigate the staleness of the rug_pull_monitor daemon."""
    if not check_daemon_status():
        print("The rug_pull_monitor daemon is not running.")
        return

    last_run_time = get_last_run_time()
    if last_run_time is None:
        print("Could not determine the last run time of the rug_pull_monitor daemon.")
        return

    current_time = datetime.now()
    time_diff = current_time - last_run_time

    if time_diff > timedelta(hours=1):
        print(f"The rug_pull_monitor daemon is stale. Last run time: {last_run_time}, Current time: {current_time}")
        print("Possible reasons for staleness:")
        print("1. The daemon might be stuck in an infinite loop or deadlock.")
        print("2. There might be a high load on the system causing delays.")
        print("3. The daemon might be waiting for a resource that is not available.")
        print("4. There might be an issue with the daemon's configuration or dependencies.")

        # Propose a fix
        print("\nProposed fix:")
        print("1. Restart the daemon to see if it resolves the issue.")
        print("2. Check the system logs for any errors or warnings related to the daemon.")
        print("3. Monitor the system resources to identify any bottlenecks.")
        print("4. Review the daemon's configuration and dependencies to ensure they are correct.")
    else:
        print("The rug_pull_monitor daemon is running and not stale.")

if __name__ == "__main__":
    investigate_staleness()