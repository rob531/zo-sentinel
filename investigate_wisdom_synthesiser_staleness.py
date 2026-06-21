import os
import time
import subprocess
from datetime import datetime, timedelta

# Configuration
WISDOM_SYNTHESISER_DAEMON = "wisdom_synthesiser"
CYCLE_THRESHOLD = timedelta(hours=6)  # 6 hours threshold
LOG_FILE = "/var/log/zo-sentinel/wisdom_synthesiser.log"
PID_FILE = "/var/run/wisdom_synthesiser.pid"

def check_daemon_staleness():
    """Check if the wisdom_synthesiser daemon is stale."""
    try:
        # Check if the daemon is running
        pid = int(open(PID_FILE).read().strip())
        process = subprocess.Popen(['ps', '-p', str(pid), '-o', 'start_time='], stdout=subprocess.PIPE)
        start_time = process.communicate()[0].decode().strip()

        # Parse start time
        start_datetime = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        current_time = datetime.now()
        uptime = current_time - start_datetime

        if uptime > CYCLE_THRESHOLD:
            print(f"Daemon is stale. Uptime: {uptime}")
            return True
        else:
            print(f"Daemon is running normally. Uptime: {uptime}")
            return False
    except (IOError, ValueError):
        print("Daemon is not running or PID file is missing.")
        return True

def restart_daemon():
    """Restart the wisdom_synthesiser daemon."""
    try:
        print("Restarting wisdom_synthesiser daemon...")
        subprocess.call(['systemctl', 'restart', WISDOM_SYNTHESISER_DAEMON])
        time.sleep(2)  # Wait for the daemon to restart
        print("Daemon restarted successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to restart daemon: {e}")

def investigate_logs():
    """Investigate logs for potential issues."""
    try:
        with open(LOG_FILE, 'r') as f:
            logs = f.readlines()
            for line in logs[-100:]:  # Check last 100 lines
                if "error" in line.lower() or "warning" in line.lower():
                    print(f"Potential issue found in logs: {line.strip()}")
    except IOError:
        print("Log file not found or inaccessible.")

def main():
    if check_daemon_staleness():
        investigate_logs()
        restart_daemon()
    else:
        print("No action required. Daemon is running normally.")

if __name__ == "__main__":
    main()