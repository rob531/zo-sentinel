#!/usr/bin/env python3
import os
import psutil
import logging
import subprocess
import time
from datetime import datetime
import json
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('zo_sentinel_builder_staleness_investigation.log'),
        logging.StreamHandler()
    ]
)

class ZOSentinelBuilderDiagnostic:
    def __init__(self):
        self.builder_process = None
        self.queue_length = 0
        self.dependency_issues = []
        self.resource_usage = {}
        self.last_build_time = None
        self.config = self._load_config()

    def _load_config(self):
        """Load configuration from a JSON file."""
        try:
            with open('zo_sentinel_builder_config.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logging.warning("Configuration file not found. Using defaults.")
            return {
                'builder_executable': 'zo_sentinel_builder',
                'queue_service_url': 'http://localhost:8080/queue',
                'dependency_check_url': 'http://localhost:8080/dependencies',
                'max_queue_length': 100,
                'min_memory_mb': 512,
                'min_cpu_cores': 2
            }

    def check_builder_process(self):
        """Check if the builder process is running."""
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] == self.config['builder_executable']:
                self.builder_process = proc
                logging.info(f"Builder process found: PID {proc.pid}")
                return True
        logging.warning("Builder process not found.")
        return False

    def check_build_queue(self):
        """Check the build queue length."""
        try:
            response = requests.get(self.config['queue_service_url'])
            if response.status_code == 200:
                queue_data = response.json()
                self.queue_length = queue_data.get('length', 0)
                logging.info(f"Current queue length: {self.queue_length}")
                if self.queue_length > self.config['max_queue_length']:
                    logging.warning(f"Queue length exceeds threshold: {self.queue_length} > {self.config['max_queue_length']}")
                return True
            else:
                logging.error(f"Failed to fetch queue data. Status code: {response.status_code}")
        except requests.RequestException as e:
            logging.error(f"Error checking build queue: {e}")
        return False

    def check_dependencies(self):
        """Check for dependency issues."""
        try:
            response = requests.get(self.config['dependency_check_url'])
            if response.status_code == 200:
                dependency_data = response.json()
                self.dependency_issues = dependency_data.get('issues', [])
                if self.dependency_issues:
                    logging.warning(f"Found {len(self.dependency_issues)} dependency issues.")
                    for issue in self.dependency_issues:
                        logging.warning(f"Dependency issue: {issue}")
                return True
            else:
                logging.error(f"Failed to fetch dependency data. Status code: {response.status_code}")
        except requests.RequestException as e:
            logging.error(f"Error checking dependencies: {e}")
        return False

    def check_resource_usage(self):
        """Check system resource usage."""
        self.resource_usage = {
            'memory': psutil.virtual_memory().percent,
            'cpu': psutil.cpu_percent(interval=1),
            'disk': psutil.disk_usage('/').percent
        }
        logging.info(f"Resource usage - Memory: {self.resource_usage['memory']}%, CPU: {self.resource_usage['cpu']}%, Disk: {self.resource_usage['disk']}%")

        if self.resource_usage['memory'] > 90:
            logging.warning("High memory usage detected.")
        if self.resource_usage['cpu'] > 90:
            logging.warning("High CPU usage detected.")
        if self.resource_usage['disk'] > 90:
            logging.warning("High disk usage detected.")

        return True

    def check_last_build_time(self):
        """Check the last build time."""
        try:
            # This is a placeholder. Replace with actual logic to fetch the last build time.
            # For example, you might check a log file or query a database.
            last_build_file = 'last_build_time.txt'
            if os.path.exists(last_build_file):
                with open(last_build_file, 'r') as f:
                    last_build_str = f.read().strip()
                    self.last_build_time = datetime.fromisoformat(last_build_str)
                    logging.info(f"Last build time: {self.last_build_time}")
                    return True
            else:
                logging.warning("Last build time file not found.")
        except Exception as e:
            logging.error(f"Error checking last build time: {e}")
        return False

    def run_diagnostics(self):
        """Run all diagnostic checks."""
        logging.info("Starting diagnostic checks...")

        self.check_builder_process()
        self.check_build_queue()
        self.check_dependencies()
        self.check_resource_usage()
        self.check_last_build_time()

        logging.info("Diagnostic checks completed.")

    def propose_remediation(self):
        """Propose remediation steps based on findings."""
        logging.info("Proposing remediation steps...")

        remediation_steps = []

        if not self.builder_process:
            remediation_steps.append("1. Start the zo_sentinel_builder process.")

        if self.queue_length > self.config['max_queue_length']:
            remediation_steps.append(f"2. Investigate and reduce the build queue length (current: {self.queue_length}).")

        if self.dependency_issues:
            remediation_steps.append("3. Resolve dependency issues:")
            for issue in self.dependency_issues:
                remediation_steps.append(f"   - {issue}")

        if self.resource_usage['memory'] > 90:
            remediation_steps.append("4. Free up memory or add more RAM to the system.")
        if self.resource_usage['cpu'] > 90:
            remediation_steps.append("5. Reduce CPU load or add more CPU cores.")
        if self.resource_usage['disk'] > 90:
            remediation_steps.append("6. Free up disk space or add more storage.")

        if not self.last_build_time or (datetime.now() - self.last_build_time).total_seconds() > 3600:
            remediation_steps.append("7. Investigate why builds are not being processed.")

        if not remediation_steps:
            remediation_steps.append("No critical issues found. System appears to be healthy.")

        for step in remediation_steps:
            logging.info(step)

        logging.info("Remediation steps proposed.")

if __name__ == "__main__":
    diagnostic = ZOSentinelBuilderDiagnostic()
    diagnostic.run_diagnostics()
    diagnostic.propose_remediation()
    logging.info("Diagnostic script completed successfully.")