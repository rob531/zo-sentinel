#!/usr/bin/env python3

import os
import subprocess
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_mcp_scanner_logs(log_path="/var/log/mcp_scanner.log", max_staleness_hours=24):
    """
    Check the mcp_scanner logs for recent activity and identify staleness.

    Args:
        log_path (str): Path to the mcp_scanner log file.
        max_staleness_hours (int): Maximum allowed staleness in hours.

    Returns:
        tuple: (is_stale, last_heartbeat_time, staleness_hours)
    """
    try:
        if not os.path.exists(log_path):
            logger.error(f"Log file not found: {log_path}")
            return True, None, None

        with open(log_path, 'r') as f:
            lines = f.readlines()

        last_heartbeat_time = None
        for line in reversed(lines):
            if "HEARTBEAT" in line:
                try:
                    # Extract timestamp from the log line
                    timestamp_str = line.split()[0] + " " + line.split()[1]
                    last_heartbeat_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    break
                except (IndexError, ValueError) as e:
                    logger.warning(f"Failed to parse timestamp from log line: {line}. Error: {e}")
                    continue

        if last_heartbeat_time is None:
            logger.warning("No heartbeat found in the logs.")
            return True, None, None

        current_time = datetime.now()
        staleness = current_time - last_heartbeat_time
        staleness_hours = staleness.total_seconds() / 3600

        is_stale = staleness_hours > max_staleness_hours

        return is_stale, last_heartbeat_time, staleness_hours

    except Exception as e:
        logger.error(f"Error checking logs: {e}")
        return True, None, None

def check_mcp_scanner_process():
    """
    Check if the mcp_scanner process is running.

    Returns:
        bool: True if the process is running, False otherwise.
    """
    try:
        result = subprocess.run(['pgrep', '-f', 'mcp_scanner'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error checking process: {e}")
        return False

def check_dependencies():
    """
    Check the dependencies of the mcp_scanner daemon.

    Returns:
        bool: True if all dependencies are available, False otherwise.
    """
    dependencies = ['redis-server', 'postgresql']
    missing_deps = []

    for dep in dependencies:
        try:
            result = subprocess.run(['which', dep], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                missing_deps.append(dep)
        except Exception as e:
            logger.error(f"Error checking dependency {dep}: {e}")
            missing_deps.append(dep)

    if missing_deps:
        logger.warning(f"Missing dependencies: {', '.join(missing_deps)}")
        return False
    return True

def check_recent_changes():
    """
    Check for recent changes in the system that might affect mcp_scanner.

    Returns:
        list: List of recent changes.
    """
    recent_changes = []

    # Check for recent updates
    try:
        result = subprocess.run(['apt', 'history'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            recent_changes.append("Recent updates:\n" + result.stdout)
    except Exception as e:
        logger.error(f"Error checking recent updates: {e}")

    # Check for recent configuration changes
    config_files = [
        '/etc/mcp_scanner/config.json',
        '/etc/redis/redis.conf',
        '/etc/postgresql/*/main/postgresql.conf'
    ]

    for config_file in config_files:
        try:
            if os.path.exists(config_file):
                stat = os.stat(config_file)
                mod_time = datetime.fromtimestamp(stat.st_mtime)
                if (datetime.now() - mod_time) < timedelta(days=7):
                    recent_changes.append(f"Recent change in {config_file} at {mod_time}")
        except Exception as e:
            logger.error(f"Error checking config file {config_file}: {e}")

    return recent_changes

def propose_solution(is_stale, last_heartbeat_time, staleness_hours, process_running, dependencies_ok, recent_changes):
    """
    Propose a solution based on the investigation results.

    Args:
        is_stale (bool): Whether the mcp_scanner is stale.
        last_heartbeat_time (datetime): Last heartbeat time.
        staleness_hours (float): Staleness in hours.
        process_running (bool): Whether the process is running.
        dependencies_ok (bool): Whether dependencies are available.
        recent_changes (list): List of recent changes.

    Returns:
        str: Proposed solution.
    """
    solution = []

    if not process_running:
        solution.append("1. Start the mcp_scanner process: `sudo systemctl start mcp_scanner`")
    else:
        solution.append("1. The mcp_scanner process is running.")

    if not dependencies_ok:
        solution.append("2. Install missing dependencies and restart the mcp_scanner process.")
    else:
        solution.append("2. All dependencies are available.")

    if is_stale:
        solution.append(f"3. The mcp_scanner is stale. Last heartbeat was at {last_heartbeat_time} ({staleness_hours:.2f} hours ago).")
        solution.append("   - Check the logs for errors: `tail -f /var/log/mcp_scanner.log`")
        solution.append("   - Restart the mcp_scanner process: `sudo systemctl restart mcp_scanner`")
    else:
        solution.append("3. The mcp_scanner is not stale.")

    if recent_changes:
        solution.append("4. Recent changes that might affect mcp_scanner:")
        for change in recent_changes:
            solution.append(f"   - {change}")
        solution.append("   - Review these changes and ensure they are compatible with mcp_scanner.")

    return "\n".join(solution)

def main():
    logger.info("Starting investigation of mcp_scanner staleness...")

    # Check logs
    is_stale, last_heartbeat_time, staleness_hours = check_mcp_scanner_logs()
    logger.info(f"Staleness check: {'Stale' if is_stale else 'Not stale'}")
    if last_heartbeat_time:
        logger.info(f"Last heartbeat: {last_heartbeat_time}")
    if staleness_hours is not None:
        logger.info(f"Staleness: {staleness_hours:.2f} hours")

    # Check process
    process_running = check_mcp_scanner_process()
    logger.info(f"Process running: {'Yes' if process_running else 'No'}")

    # Check dependencies
    dependencies_ok = check_dependencies()
    logger.info(f"Dependencies OK: {'Yes' if dependencies_ok else 'No'}")

    # Check recent changes
    recent_changes = check_recent_changes()
    if recent_changes:
        logger.info("Recent changes found:")
        for change in recent_changes:
            logger.info(f"  - {change}")
    else:
        logger.info("No recent changes found.")

    # Propose solution
    solution = propose_solution(is_stale, last_heartbeat_time, staleness_hours, process_running, dependencies_ok, recent_changes)
    logger.info("\nProposed solution:")
    logger.info(solution)

if __name__ == "__main__":
    main()