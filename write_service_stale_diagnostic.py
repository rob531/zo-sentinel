#!/usr/bin/env python3
"""
Diagnostic utility to investigate write_service heartbeat staleness.

Purpose:
    Investigate write_service heartbeat staleness (observed: 1h31m, threshold: >300s)
    without modifying state. Determines if write_service remains functionally responsive
    via read operations despite stale heartbeat.

Behavior:
    1. Query service_health table via write_service HTTP API (SELECT only)
    2. Compare heartbeat age against STALE_THRESHOLD_SECONDS (300s)
    3. Perform functional read probe via http://127.0.0.1:8772/query
    4. Report API responsiveness with latency
    5. Log findings with timestamps

External Dependencies:
    - requests

Constraints:
    - READ-ONLY: No INSERT/UPDATE/DELETE/DROP on any table
    - Uses write_service HTTP API at 127.0.0.1:8772 exclusively
    - No direct DB access
    - No duckdb import
    - Self-contained, import-clean
    - 10s timeout for HTTP calls
"""

from __future__ import annotations

import datetime
import json
import logging
import sys
import time
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except ImportError:
    print("ERROR: 'requests' module is required but not installed.", file=sys.stderr)
    print("Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


# Configure module logger
logger = logging.getLogger(__name__)

# Constants
STALE_THRESHOLD_SECONDS: int = 300  # 5 minutes
WRITE_SERVICE_HOST: str = "127.0.0.1"
WRITE_SERVICE_PORT: int = 8772
BASE_URL: str = f"http://{WRITE_SERVICE_HOST}:{WRITE_SERVICE_PORT}"
QUERY_ENDPOINT: str = f"{BASE_URL}/query"
HEALTH_ENDPOINT: str = f"{BASE_URL}/health"
HTTP_TIMEOUT: float = 10.0  # seconds


def format_seconds_to_hhmmss(seconds: float) -> str:
    """
    Convert a duration in seconds to HH:MM:SS formatted string.

    Args:
        seconds: Duration in seconds (can be float)

    Returns:
        Formatted string in HH:MM:SS format
    """
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def get_current_timestamp() -> str:
    """Get current timestamp in ISO 8601 format."""
    return datetime.datetime.utcnow().isoformat() + "Z"


class WriteServiceDiagnostic:
    """
    Diagnostic utility for write_service heartbeat staleness investigation.

    This class provides methods to:
    - Query heartbeat age from service_health table via HTTP API
    - Determine staleness status against threshold
    - Probe functional responsiveness via read operations
    - Generate comprehensive diagnostic reports
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        stale_threshold: int = STALE_THRESHOLD_SECONDS,
        timeout: float = HTTP_TIMEOUT,
    ) -> None:
        """
        Initialize the diagnostic utility.

        Args:
            base_url: Base URL for write_service HTTP API
            stale_threshold: Threshold in seconds for staleness detection
            timeout: HTTP request timeout in seconds
        """
        self.base_url = base_url
        self.query_endpoint = f"{base_url}/query"
        self.health_endpoint = f"{base_url}/health"
        self.stale_threshold = stale_threshold
        self.timeout = timeout
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        """Get or create a requests session for connection reuse."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"Content-Type": "application/json"})
        return self._session

    def close(self) -> None:
        """Close the underlying session if it exists."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self) -> "WriteServiceDiagnostic":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()

    def _make_http_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Tuple[Optional[Any], Optional[str], float]:
        """
        Execute an HTTP request with timeout and return response data, error, and latency.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Target URL
            **kwargs: Additional arguments passed to requests

        Returns:
            Tuple of (response_data, error_message, latency_seconds)
        """
        kwargs.setdefault("timeout", self.timeout)

        try:
            start_time = time.perf_counter()
            response = self.session.request(method, url, **kwargs)
            latency = time.perf_counter() - start_time

            if response.status_code >= 400:
                return (
                    None,
                    f"HTTP {response.status_code}: {response.reason}",
                    latency,
                )

            try:
                data = response.json()
                return data, None, latency
            except json.JSONDecodeError:
                return {"raw": response.text}, None, latency

        except requests.Timeout:
            return None, "Request timed out", self.timeout
        except requests.ConnectionError:
            return None, "Connection failed", 0.0
        except requests.RequestException as e:
            return None, f"Request error: {str(e)}", 0.0

    def get_heartbeat_age(self) -> Tuple[Optional[float], Optional[str]]:
        """
        Query service_health table to get write_service heartbeat age.

        Returns:
            Tuple of (heartbeat_age_seconds, error_message)
            heartbeat_age_seconds is None if query fails
        """
        query = """
        SELECT (strftime('%s', 'now') - strftime('%s', last_heartbeat)) AS age_seconds
        FROM service_health
        WHERE service_name = 'write_service'
        LIMIT 1
        """

        payload = {"query": query.strip()}
        response_data, error, latency = self._make_http_request(
            "POST", self.query_endpoint, json=payload
        )

        if error:
            logger.error(f"Heartbeat query failed: {error}")
            return None, error

        if response_data is None:
            return None, "Empty response from server"

        if response_data.get("error"):
            error_msg = response_data["error"]
            logger.error(f"Query error: {error_msg}")
            return None, f"Query error: {error_msg}"

        rows = response_data.get("rows", [])
        if not rows:
            return None, "No heartbeat record found for write_service"

        age_seconds = rows[0][0]

        if age_seconds is None:
            return None, "Heartbeat age is NULL"

        logger.debug(
            f"Heartbeat query completed in {latency:.3f}s, age: {age_seconds}s"
        )
        return float(age_seconds), None

    def get_service_health_details(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Retrieve full service_health record for write_service.

        Returns:
            Tuple of (health_record_dict, error_message)
        """
        query = """
        SELECT service_name, last_heartbeat, status, additional_info
        FROM service_health
        WHERE service_name = 'write_service'
        LIMIT 1
        """

        payload = {"query": query.strip()}
        response_data, error, latency = self._make_http_request(
            "POST", self.query_endpoint, json=payload
        )

        if error:
            return None, error

        if response_data and response_data.get("error"):
            return None, response_data["error"]

        rows = response_data.get("rows", [])
        if not rows:
            return None, "No service health record found"

        columns = response_data.get("columns", ["service_name", "last_heartbeat", "status", "additional_info"])
        record = dict(zip(columns, rows[0]))
        return record, None

    def determine_staleness(
        self, heartbeat_age: float
    ) -> Tuple[str, str, float]:
        """
        Determine staleness status based on heartbeat age.

        Args:
            heartbeat_age: Age of heartbeat in seconds

        Returns:
            Tuple of (formatted_age, stale_status, raw_age_seconds)
            stale_status is "STALE" if age > threshold, else "OK"
        """
        formatted_age = format_seconds_to_hhmmss(heartbeat_age)
        is_stale = heartbeat_age > self.stale_threshold
        stale_status = "STALE" if is_stale else "OK"
        return formatted_age, stale_status, heartbeat_age

    def perform_read_probe(self) -> Tuple[str, Optional[float], Optional[str]]:
        """
        Perform a lightweight read probe to verify functional responsiveness.

        Executes SELECT 1 to check if the service can respond to queries.

        Returns:
            Tuple of (probe_status, latency_seconds, error_message)
            probe_status is "RESPONSIVE", "UNRESPONSIVE", or "ERROR"
        """
        probe_query = "SELECT 1 AS probe_test"
        payload = {"query": probe_query}

        response_data, error, latency = self._make_http_request(
            "POST", self.query_endpoint, json=payload
        )

        if error:
            logger.warning(f"Read probe failed: {error}")
            return "UNRESPONSIVE", None, error

        if response_data is None:
            return "ERROR", None, "Empty response"

        if response_data.get("error"):
            return "ERROR", None, response_data["error"]

        rows = response_data.get("rows", [])
        if rows and len(rows) > 0:
            logger.info(f"Read probe successful, latency: {latency:.3f}s")
            return "RESPONSIVE", latency, None

        return "ERROR", None, "Unexpected response format"

    def check_health_endpoint(self) -> Tuple[bool, Optional[str], float]:
        """
        Check if the /health endpoint is accessible.

        Returns:
            Tuple of (is_healthy, error_message, latency)
        """
        response_data, error, latency = self._make_http_request(
            "GET", self.health_endpoint
        )

        if error:
            return False, error, latency

        if response_data and response_data.get("status") == "healthy":
            return True, None, latency

        return False, "Unexpected health response", latency

    def run_diagnostic(self) -> Dict[str, Any]:
        """
        Execute complete diagnostic suite and return findings.

        Performs all checks and assembles a comprehensive diagnostic report.
        This method does not modify any state.

        Returns:
            Dictionary containing all diagnostic findings
        """
        timestamp = get_current_timestamp()
        findings: Dict[str, Any] = {
            "timestamp": timestamp,
            "stale_threshold_seconds": self.stale_threshold,
            "api_base_url": self.base_url,
            "checks": {},
        }

        # Check 1: Get heartbeat age and determine staleness
        heartbeat_age, heartbeat_error = self.get_heartbeat_age()

        if heartbeat_error:
            findings["checks"]["heartbeat"] = {
                "status": "ERROR",
                "error": heartbeat_error,
                "formatted_age": "N/A",
                "stale_status": "UNKNOWN",
            }
        else:
            formatted_age, stale_status, raw_age = self.determine_staleness(
                heartbeat_age
            )
            findings["checks"]["heartbeat"] = {
                "status": "OK",
                "formatted_age": formatted_age,
                "raw_age_seconds": raw_age,
                "stale_status": stale_status,
            }

        # Check 2: Functional read probe
        probe_status, probe_latency, probe_error = self.perform_read_probe()
        probe_result: Dict[str, Any] = {
            "status": probe_status,
        }
        if probe_latency is not None:
            probe_result["latency_seconds"] = round(probe_latency, 3)
        if probe_error:
            probe_result["error"] = probe_error

        findings["checks"]["read_probe"] = probe_result

        # Check 3: Health endpoint (optional)
        is_healthy, health_error, health_latency = self.check_health_endpoint()
        findings["checks"]["health_endpoint"] = {
            "is_healthy": is_healthy,
            "latency_seconds": round(health_latency, 3) if is_healthy else None,
            "error": health_error,
        }

        # Summary assessment
        findings["summary"] = self._generate_summary(findings)

        return findings

    def _generate_summary(self, findings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a human-readable summary of diagnostic findings.

        Args:
            findings: Complete diagnostic findings dictionary

        Returns:
            Summary dictionary with key assessments
        """
        summary: Dict[str, Any] = {
            "is_operational": False,
            "concerns": [],
            "recommendations": [],
        }

        heartbeat_check = findings.get("checks", {}).get("heartbeat", {})
        read_probe_check = findings.get("checks", {}).get("read_probe", {})

        # Assess operational status
        if read_probe_check.get("status") == "RESPONSIVE":
            summary["is_operational"] = True

        # Identify concerns
        if heartbeat_check.get("stale_status") == "STALE":
            summary["concerns"].append(
                f"Heartbeat is stale: {heartbeat_check.get('formatted_age')} "
                f"(threshold: {format_seconds_to_hhmmss(self.stale_threshold)})"
            )
            summary["recommendations"].append(
                "Investigate write_service process health and network connectivity"
            )

        if heartbeat_check.get("status") == "ERROR":
            summary["concerns"].append(
                f"Cannot retrieve heartbeat status: {heartbeat_check.get('error')}"
            )

        if read_probe_check.get("status") != "RESPONSIVE":
            summary["is_operational"] = False
            summary["concerns"].append(
                f"Read probe failed: {read_probe_check.get('error', 'Unknown error')}"
            )
            summary["recommendations"].append(
                "write_service is not responsive - check service logs and process status"
            )

        # Positive indicators
        if heartbeat_check.get("stale_status") == "OK":
            summary["recommendations"].append(
                "Heartbeat is healthy, no action needed"
            )

        if read_probe_check.get("status") == "RESPONSIVE":
            summary["recommendations"].append(
                "write_service is functionally responsive via read operations"
            )

        return summary

    def print_report(self, findings: Dict[str, Any]) -> None:
        """
        Print formatted diagnostic report to stdout.

        Args:
            findings: Complete diagnostic findings dictionary
        """
        print("\n" + "=" * 60)
        print("WRITE_SERVICE STALE_DIAGNOSTIC REPORT")
        print("=" * 60)
        print(f"Timestamp:        {findings['timestamp']}")
        print(f"API Endpoint:     {findings['api_base_url']}")
        print(f"Stale Threshold:  {findings['stale_threshold_seconds']}s")
        print("-" * 60)

        # Heartbeat check
        heartbeat = findings.get("checks", {}).get("heartbeat", {})
        print("\n[HEARTBEAT CHECK]")
        print(f"  Status:         {heartbeat.get('status', 'UNKNOWN')}")
        print(f"  Heartbeat Age:  {heartbeat.get('formatted_age', 'N/A')}")
        print(f"  Stale Status:   {heartbeat.get('stale_status', 'UNKNOWN')}")
        if heartbeat.get("error"):
            print(f"  Error:          {heartbeat.get('error')}")

        # Read probe check
        read_probe = findings.get("checks", {}).get("read_probe", {})
        print("\n[READ PROBE CHECK]")
        print(f"  Status:         {read_probe.get('status', 'UNKNOWN')}")
        if read_probe.get("latency_seconds") is not None:
            print(f"  Latency:        {read_probe.get('latency_seconds')}s")
        if read_probe.get("error"):
            print(f"  Error:          {read_probe.get('error')}")

        # Health endpoint check
        health_ep = findings.get("checks", {}).get("health_endpoint", {})
        print("\n[HEALTH ENDPOINT CHECK]")
        print(f"  Healthy:        {health_ep.get('is_healthy', 'UNKNOWN')}")
        if health_ep.get("latency_seconds"):
            print(f"  Latency:        {health_ep.get('latency_seconds')}s")
        if health_ep.get("error"):
            print(f"  Error:          {health_ep.get('error')}")

        # Summary
        summary = findings.get("summary", {})
        print("\n" + "-" * 60)
        print("[SUMMARY]")
        print(f"  Operational:    {summary.get('is_operational', False)}")

        if summary.get("concerns"):
            print("\n  Concerns:")
            for concern in summary["concerns"]:
                print(f"    - {concern}")

        if summary.get("recommendations"):
            print("\n  Recommendations:")
            for rec in summary["recommendations"]:
                print(f"    - {rec}")

        print("\n" + "=" * 60)

    def log_report(self, findings: Dict[str, Any]) -> None:
        """
        Log diagnostic findings to the configured logger.

        Args:
            findings: Complete diagnostic findings dictionary
        """
        heartbeat = findings.get("checks", {}).get("heartbeat", {})
        read_probe = findings.get("checks", {}).get("read_probe", {})

        # Log heartbeat information
        hb_status = heartbeat.get("stale_status", "UNKNOWN")
        hb_age = heartbeat.get("formatted_age", "N/A")
        logger.info(
            f"Heartbeat Age: {hb_age} | Stale Status: {hb_status}"
        )

        # Log read probe information
        probe_status = read_probe.get("status", "UNKNOWN")
        probe_latency = read_probe.get("latency_seconds")
        latency_str = f"{probe_latency}s" if probe_latency else "N/A"
        logger.info(
            f"API Probe Result: {probe_status} | Latency: {latency_str}"
        )

        # Log overall assessment
        is_operational = findings.get("summary", {}).get("is_operational", False)
        operational_str = "OPERATIONAL" if is_operational else "NOT OPERATIONAL"
        logger.info(f"Overall Status: {operational_str}")


def run_quick_diagnostic() -> int:
    """
    Run a quick diagnostic and print results.

    Returns:
        Exit code (0 for success, non-zero for fatal error)
    """
    try:
        with WriteServiceDiagnostic() as diagnostic:
            findings = diagnostic.run_diagnostic()
            diagnostic.print_report(findings)
            diagnostic.log_report(findings)
            return 0
    except Exception as e:
        logger.error(f"Fatal error during diagnostic: {str(e)}")
        print(f"FATAL ERROR: {str(e)}", file=sys.stderr)
        return 1


def main() -> int:
    """
    Main entry point for the diagnostic utility.

    Returns:
        Exit code (0 for success, non-zero for fatal error)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    return run_quick_diagnostic()


# Test scenarios for if __name__ == '__main__' block
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Write Service Stale Diagnostic Utility"
    )
    parser.add_argument(
        "--endpoint",
        default=BASE_URL,
        help=f"Base URL for write_service API (default: {BASE_URL})",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=STALE_THRESHOLD_SECONDS,
        help=f"Stale threshold in seconds (default: {STALE_THRESHOLD_SECONDS})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--test",
        choices=["success", "stale_heartbeat", "unresponsive", "connection_error"],
        help="Run specific test scenario (for testing purposes)",
    )

    args = parser.parse_args()

    # Test scenarios for validation
    if args.test:
        print(f"\nRunning test scenario: {args.test}")
        print("-" * 40)

        if args.test == "success":
            # Simulate successful diagnostic response
            print("Simulating: Service responsive, heartbeat fresh")
            print("Expected: Operational, OK")
            diagnostic = WriteServiceDiagnostic(base_url=args.endpoint, stale_threshold=args.threshold)
            heartbeat_age, hb_error = diagnostic.get_heartbeat_age()
            probe_status, probe_latency, probe_error = diagnostic.perform_read_probe()
            if hb_error:
                print(f"  Heartbeat Error: {hb_error}")
            if probe_error:
                print(f"  Probe Error: {probe_error}")
            print(f"  Probe Status: {probe_status}")

        elif args.test == "stale_heartbeat":
            # Simulate stale heartbeat scenario
            print("Simulating: Heartbeat stale (>300s), but service responsive")
            print("Expected: STALE status, but RESPONSIVE probe")
            print("  Heartbeat Age: 01:31:00 (1h31m)")
            print("  Stale Status: STALE")
            print("  Probe Status: RESPONSIVE")
            print("  Operational: True (functional despite staleness)")

        elif args.test == "unresponsive":
            # Simulate unresponsive service
            print("Simulating: Service unresponsive to queries")
            print("Expected: ERROR on probe, NOT OPERATIONAL")
            diagnostic = WriteServiceDiagnostic(base_url=args.endpoint, stale_threshold=args.threshold)
            probe_status, probe_latency, probe_error = diagnostic.perform_read_probe()
            print(f"  Probe Status: {probe_status}")
            if probe_error:
                print(f"  Probe Error: {probe_error}")

        elif args.test == "connection_error":
            # Simulate connection failure
            print("Simulating: Cannot connect to write_service")
            print("Expected: CONNECTION_FAILED error")
            diagnostic = WriteServiceDiagnostic(base_url="http://127.0.0.1:9999", stale_threshold=args.threshold)
            probe_status, probe_latency, probe_error = diagnostic.perform_read_probe()
            print(f"  Probe Status: {probe_status}")
            if probe_error:
                print(f"  Error: {probe_error}")

        print("-" * 40)
        print("Test scenario complete.")
        sys.exit(0)

    # Normal operation
    if args.json:
        # JSON output mode
        try:
            with WriteServiceDiagnostic(base_url=args.endpoint, stale_threshold=args.threshold) as diagnostic:
                findings = diagnostic.run_diagnostic()
                print(json.dumps(findings, indent=2))
                sys.exit(0)
        except Exception as e:
            error_response = {
                "error": str(e),
                "timestamp": get_current_timestamp(),
            }
            print(json.dumps(error_response, indent=2), file=sys.stderr)
            sys.exit(1)
    else:
        # Normal text output mode
        sys.exit(main())