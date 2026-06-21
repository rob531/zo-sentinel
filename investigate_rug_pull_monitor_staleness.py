#!/usr/bin/env python3
import subprocess
import json
import os
import time
from datetime import datetime, timedelta
import psutil
import logging
import argparse

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_service_status(service_name):
    """Check the status of the rug_pull_monitor service."""
    try:
        result = subprocess.run(['systemctl', 'status', service_name], capture_output=True, text=True)
        return result.stdout
    except Exception as e:
        logger.error(f"Error checking service status: {e}")
        return None

def get_service_logs(service_name, hours=24):
    """Retrieve logs for the rug_pull_monitor service."""
    try:
        hours_ago = datetime.now() - timedelta(hours=hours)
        timestamp = hours_ago.strftime('%Y-%m-%d %H:%M:%S')
        result = subprocess.run(['journalctl', '-u', service_name, '--since', timestamp], capture_output=True, text=True)
        return result.stdout
    except Exception as e:
        logger.error(f"Error retrieving service logs: {e}")
        return None

def get_system_metrics():
    """Gather system performance metrics."""
    metrics = {
        'cpu_usage': psutil.cpu_percent(interval=1),
        'memory_usage': psutil.virtual_memory().percent,
        'disk_usage': psutil.disk_usage('/').percent,
        'open_files': len(psutil.open_files()),
        'network_connections': len(psutil.net_connections())
    }
    return metrics

def analyze_logs(logs):
    """Analyze logs for recurring errors or patterns."""
    error_patterns = [
        'ERROR',
        'Exception',
        'Timeout',
        'Connection refused',
        'Failed to',
        'Retrying',
        'Stale heartbeat'
    ]
    errors = []
    for pattern in error_patterns:
        if pattern in logs:
            errors.append(pattern)
    return errors

def generate_report(service_status, logs, metrics, errors):
    """Generate a diagnostic report."""
    report = {
        'timestamp': datetime.now().isoformat(),
        'service_status': service_status,
        'system_metrics': metrics,
        'errors_found': errors,
        'log_snippet': logs[-1000:] if logs else 'No logs retrieved'
    }
    return report

def main():
    parser = argparse.ArgumentParser(description='Investigate staleness of the rug_pull_monitor daemon.')
    parser.add_argument('--service', default='rug_pull_monitor', help='Name of the service to investigate')
    parser.add_argument('--hours', type=int, default=24, help='Number of hours of logs to retrieve')
    args = parser.parse_args()

    logger.info(f"Investigating staleness of {args.service} service...")

    service_status = get_service_status(args.service)
    logs = get_service_logs(args.service, args.hours)
    metrics = get_system_metrics()
    errors = analyze_logs(logs) if logs else []

    report = generate_report(service_status, logs, metrics, errors)

    # Save report to a file
    report_file = f"rug_pull_monitor_diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=4)

    logger.info(f"Diagnostic report generated: {report_file}")

    # Print summary
    print("\n=== Diagnostic Summary ===")
    print(f"Service Status: {service_status.splitlines()[0] if service_status else 'Unknown'}")
    print(f"Errors Found: {', '.join(errors) if errors else 'None'}")
    print(f"CPU Usage: {metrics['cpu_usage']}%")
    print(f"Memory Usage: {metrics['memory_usage']}%")
    print(f"Disk Usage: {metrics['disk_usage']}%")
    print(f"Report saved to: {report_file}")

if __name__ == "__main__":
    main()