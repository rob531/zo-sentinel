#!/usr/bin/env python3
import subprocess
import os
import datetime
import json
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('write_service_staleness_investigation.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def gather_system_metrics():
    """Gather system metrics that might be relevant to the write_service daemon."""
    metrics = {}

    # CPU usage
    try:
        cpu_usage = subprocess.check_output(['top', '-bn1', '-p', '1']).decode('utf-8')
        metrics['cpu_usage'] = cpu_usage
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to gather CPU usage: {e}")
        metrics['cpu_usage'] = str(e)

    # Memory usage
    try:
        memory_usage = subprocess.check_output(['free', '-h']).decode('utf-8')
        metrics['memory_usage'] = memory_usage
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to gather memory usage: {e}")
        metrics['memory_usage'] = str(e)

    # Disk usage
    try:
        disk_usage = subprocess.check_output(['df', '-h']).decode('utf-8')
        metrics['disk_usage'] = disk_usage
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to gather disk usage: {e}")
        metrics['disk_usage'] = str(e)

    return metrics

def gather_write_service_logs():
    """Gather logs related to the write_service daemon."""
    logs = {}

    # Check if the write_service log file exists
    log_file = '/var/log/write_service.log'
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r') as f:
                logs['write_service_log'] = f.read()
        except IOError as e:
            logger.error(f"Failed to read write_service log: {e}")
            logs['write_service_log'] = str(e)
    else:
        logger.warning(f"Write service log file not found at {log_file}")
        logs['write_service_log'] = f"Log file not found at {log_file}"

    return logs

def gather_write_service_status():
    """Gather the status of the write_service daemon."""
    status = {}

    # Check if the write_service is running
    try:
        status['is_running'] = subprocess.check_output(['systemctl', 'is-active', 'write_service']).decode('utf-8').strip() == 'active'
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to check write_service status: {e}")
        status['is_running'] = False

    # Get the write_service unit file
    try:
        unit_file = subprocess.check_output(['systemctl', 'cat', 'write_service']).decode('utf-8')
        status['unit_file'] = unit_file
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get write_service unit file: {e}")
        status['unit_file'] = str(e)

    return status

def gather_network_metrics():
    """Gather network metrics that might be relevant to the write_service daemon."""
    metrics = {}

    # Network connections
    try:
        network_connections = subprocess.check_output(['ss', '-tuln']).decode('utf-8')
        metrics['network_connections'] = network_connections
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to gather network connections: {e}")
        metrics['network_connections'] = str(e)

    return metrics

def main():
    logger.info("Starting write_service staleness investigation...")

    # Create a directory to store the investigation results
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    results_dir = f'write_service_staleness_investigation_{timestamp}'
    Path(results_dir).mkdir(parents=True, exist_ok=True)

    # Gather all relevant information
    system_metrics = gather_system_metrics()
    write_service_logs = gather_write_service_logs()
    write_service_status = gather_write_service_status()
    network_metrics = gather_network_metrics()

    # Combine all gathered information
    investigation_results = {
        'timestamp': timestamp,
        'system_metrics': system_metrics,
        'write_service_logs': write_service_logs,
        'write_service_status': write_service_status,
        'network_metrics': network_metrics
    }

    # Save the investigation results to a file
    results_file = os.path.join(results_dir, 'investigation_results.json')
    with open(results_file, 'w') as f:
        json.dump(investigation_results, f, indent=4)

    logger.info(f"Investigation completed. Results saved to {results_file}")

if __name__ == '__main__':
    main()