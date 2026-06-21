#!/usr/bin/env python3
import os
import sys
import time
import logging
import subprocess
import psutil
import socket
import requests
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

class WriteServiceStalenessInvestigator:
    def __init__(self):
        self.write_service_name = "write_service"
        self.upstream_services = ["auth_service", "db_service", "cache_service"]
        self.required_ports = [8080, 5432, 6379]
        self.min_memory_mb = 100
        self.min_cpu_percent = 10

    def check_service_running(self):
        """Check if write_service is running"""
        try:
            for proc in psutil.process_iter(['name']):
                if self.write_service_name in proc.info['name']:
                    return True
            return False
        except Exception as e:
            logging.error(f"Error checking service status: {e}")
            return False

    def check_network_connectivity(self):
        """Check network connectivity to upstream services"""
        results = {}
        for service in self.upstream_services:
            try:
                # Simple ping check (replace with actual service endpoint checks)
                response = subprocess.run(
                    ['ping', '-c', '1', service],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5
                )
                if response.returncode == 0:
                    results[service] = "OK"
                else:
                    results[service] = "FAILED"
            except Exception as e:
                results[service] = f"ERROR: {str(e)}"
        return results

    def check_port_availability(self):
        """Check if required ports are available"""
        results = {}
        for port in self.required_ports:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    result = s.connect_ex(('localhost', port))
                    if result == 0:
                        results[port] = "OPEN"
                    else:
                        results[port] = "CLOSED"
            except Exception as e:
                results[port] = f"ERROR: {str(e)}"
        return results

    def check_system_resources(self):
        """Check system resource availability"""
        results = {}

        # Check memory
        mem = psutil.virtual_memory()
        if mem.available / (1024 * 1024) < self.min_memory_mb:
            results['memory'] = f"LOW: {mem.available / (1024 * 1024):.2f}MB available"
        else:
            results['memory'] = f"OK: {mem.available / (1024 * 1024):.2f}MB available"

        # Check CPU
        cpu_percent = psutil.cpu_percent(interval=1)
        if cpu_percent > (100 - self.min_cpu_percent):
            results['cpu'] = f"HIGH: {cpu_percent}% usage"
        else:
            results['cpu'] = f"OK: {cpu_percent}% usage"

        return results

    def check_upstream_service_health(self):
        """Check health of upstream services"""
        results = {}
        for service in self.upstream_services:
            try:
                # Replace with actual health check endpoints
                response = requests.get(f"http://{service}/health", timeout=5)
                if response.status_code == 200:
                    results[service] = "HEALTHY"
                else:
                    results[service] = f"UNHEALTHY: HTTP {response.status_code}"
            except Exception as e:
                results[service] = f"ERROR: {str(e)}"
        return results

    def analyze_logs(self):
        """Analyze write_service logs for errors"""
        try:
            log_file = f"/var/log/{self.write_service_name}.log"
            if not os.path.exists(log_file):
                return {"log_analysis": "Log file not found"}

            with open(log_file, 'r') as f:
                lines = f.readlines()[-100:]  # Check last 100 lines

            errors = [line.strip() for line in lines if "ERROR" in line]
            if errors:
                return {"log_analysis": f"Found {len(errors)} errors", "errors": errors}
            else:
                return {"log_analysis": "No recent errors found"}
        except Exception as e:
            return {"log_analysis": f"ERROR: {str(e)}"}

    def run_investigation(self):
        """Run all checks and compile results"""
        logging.info("Starting write_service staleness investigation")

        results = {
            "timestamp": datetime.now().isoformat(),
            "service_running": self.check_service_running(),
            "network_connectivity": self.check_network_connectivity(),
            "port_availability": self.check_port_availability(),
            "system_resources": self.check_system_resources(),
            "upstream_health": self.check_upstream_service_health(),
            "log_analysis": self.analyze_logs()
        }

        # Generate recommendations
        recommendations = []
        if not results["service_running"]:
            recommendations.append("Write service is not running. Check service configuration and start it.")

        for service, status in results["network_connectivity"].items():
            if "FAILED" in status or "ERROR" in status:
                recommendations.append(f"Network connectivity issue with {service}. Check network configuration.")

        for port, status in results["port_availability"].items():
            if status == "CLOSED":
                recommendations.append(f"Required port {port} is closed. Check firewall settings.")

        for resource, status in results["system_resources"].items():
            if "LOW" in status or "HIGH" in status:
                recommendations.append(f"System resource {resource} is under pressure. Consider scaling up.")

        for service, status in results["upstream_health"].items():
            if "UNHEALTHY" in status or "ERROR" in status:
                recommendations.append(f"Upstream service {service} is unhealthy. Investigate this service.")

        if "errors" in results["log_analysis"]:
            recommendations.append(f"Found errors in logs. Check recent log entries for details.")

        results["recommendations"] = recommendations

        logging.info("Investigation completed")
        return results

if __name__ == "__main__":
    investigator = WriteServiceStalenessInvestigator()
    results = investigator.run_investigation()

    # Print summary
    print("\nInvestigation Summary:")
    print(f"Timestamp: {results['timestamp']}")
    print(f"Service Running: {'Yes' if results['service_running'] else 'No'}")
    print("\nRecommendations:")
    for i, rec in enumerate(results["recommendations"], 1):
        print(f"{i}. {rec}")

    # Save detailed results to file
    with open('write_service_staleness_report.json', 'w') as f:
        import json
        json.dump(results, f, indent=2)

    sys.exit(0 if not results["recommendations"] else 1)