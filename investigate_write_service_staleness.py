#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('write_service_staleness_investigation.log'),
        logging.StreamHandler()
    ]
)

def check_process_status(pid):
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def get_write_service_pid():
    """Get the PID of the write_service daemon."""
    try:
        with open('/var/run/write_service.pid', 'r') as f:
            pid = int(f.read().strip())
            return pid
    except (IOError, ValueError):
        logging.error("Could not read or parse write_service PID file.")
        return None

def check_deadlocks(pid):
    """Check for deadlocks in the write_service process."""
    try:
        # Use gdb to check for deadlocks
        gdb_output = subprocess.check_output(
            ['gdb', '-batch', '-p', str(pid), '-ex', 'thread apply all bt'],
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        if 'deadlock' in gdb_output.lower():
            logging.warning("Potential deadlock detected in write_service.")
            return True
        return False
    except subprocess.CalledProcessError as e:
        logging.error(f"Error checking for deadlocks: {e.output}")
        return False

def check_resource_exhaustion(pid):
    """Check for resource exhaustion in the write_service process."""
    try:
        # Check memory usage
        mem_info = subprocess.check_output(
            ['ps', '-p', str(pid), '-o', '%mem,rss'],
            universal_newlines=True
        ).strip().split('\n')[1]
        mem_percent, mem_rss = mem_info.split()
        if float(mem_percent) > 90:
            logging.warning(f"High memory usage detected: {mem_percent}%")

        # Check CPU usage
        cpu_info = subprocess.check_output(
            ['ps', '-p', str(pid), '-o', '%cpu'],
            universal_newlines=True
        ).strip().split('\n')[1]
        cpu_percent = cpu_info.strip()
        if float(cpu_percent) > 90:
            logging.warning(f"High CPU usage detected: {cpu_percent}%")

        return True if float(mem_percent) > 90 or float(cpu_percent) > 90 else False
    except subprocess.CalledProcessError as e:
        logging.error(f"Error checking resource usage: {e.output}")
        return False

def check_network_connectivity():
    """Check network connectivity to the write_service's dependencies."""
    dependencies = [
        'db.example.com:5432',
        'cache.example.com:6379',
        'api.example.com:8080'
    ]
    for dep in dependencies:
        try:
            # Use netcat to check connectivity
            subprocess.check_output(
                ['nc', '-z', '-w', '3', dep],
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            logging.info(f"Network connectivity to {dep} is OK.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Network connectivity to {dep} failed: {e.output}")
            return False
    return True

def restart_write_service():
    """Restart the write_service daemon."""
    try:
        logging.info("Attempting to restart write_service...")
        subprocess.check_output(
            ['systemctl', 'restart', 'write_service'],
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        logging.info("write_service restarted successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to restart write_service: {e.output}")
        return False

def main():
    logging.info("Starting write_service staleness investigation.")

    pid = get_write_service_pid()
    if not pid:
        logging.error("write_service PID not found. Is the service installed and running?")
        sys.exit(1)

    if not check_process_status(pid):
        logging.error(f"write_service process with PID {pid} is not running.")
        if restart_write_service():
            sys.exit(0)
        else:
            sys.exit(1)

    logging.info(f"write_service process with PID {pid} is running.")

    if check_deadlocks(pid):
        logging.warning("Deadlock detected. Consider restarting the service.")
        if restart_write_service():
            sys.exit(0)

    if check_resource_exhaustion(pid):
        logging.warning("Resource exhaustion detected. Consider optimizing the service or adding more resources.")
        if restart_write_service():
            sys.exit(0)

    if not check_network_connectivity():
        logging.error("Network connectivity issues detected. Check the service's dependencies.")
        sys.exit(1)

    logging.info("No critical issues detected. The service may be experiencing temporary staleness.")
    logging.info("Consider monitoring the service for further issues.")

if __name__ == "__main__":
    main()