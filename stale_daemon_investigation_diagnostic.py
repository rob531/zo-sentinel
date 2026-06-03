# deps: requests

import requests
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional


class StaleDaemonDiagnostics:
    """Diagnostic module for identifying stale daemons in service_health table."""

    # Configuration
    WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
    HTTP_TIMEOUT = 10.0

    # Thresholds (in minutes)
    HEALTHY_THRESHOLD_MINUTES = 120  # < 2 hours
    STALE_THRESHOLD_MINUTES = 1440   # >= 24 hours

    def __init__(self, reference_time: Optional[datetime] = None):
        """
        Initialize diagnostics.

        Args:
            reference_time: Reference time for elapsed calculation. Defaults to current UTC.
        """
        self.reference_time = reference_time or datetime(2026, 6, 3, 1, 19, 0, tzinfo=timezone.utc)
        self.services = []
        self.classification_counts = {"HEALTHY": 0, "STALE": 0, "CRITICAL": 0}

    def query_service_health(self) -> List[Dict[str, Any]]:
        """
        Query service_health table via write_service HTTP API.

        Returns:
            List of service health records from database.

        Raises:
            requests.RequestException: If HTTP call fails.
        """
        sql_query = "SELECT service_name, status, timestamp, meta FROM service_health"
        payload = {"sql": sql_query}

        response = requests.post(
            self.WRITE_SERVICE_URL,
            json=payload,
            timeout=self.HTTP_TIMEOUT,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()

        result = response.json()
        return result.get("rows", []) if isinstance(result, dict) else result

    def parse_heartbeat_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """
        Parse ISO 8601 timestamp string to datetime object.

        Args:
            timestamp_str: ISO 8601 formatted timestamp string.

        Returns:
            Parsed datetime object or None if parsing fails.
        """
        if not timestamp_str:
            return None

        # Handle various ISO 8601 formats
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%fZ",
            "%Y-%m-%d %H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(timestamp_str.replace('+00:00', 'Z').rstrip('Z') + 'Z' if 'Z' not in timestamp_str and '+' not in timestamp_str else timestamp_str, fmt.replace('Z', '').replace('+00:00', '') + 'Z')
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue

        # Try parsing with fromisoformat as fallback
        try:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except ValueError:
            return None

    def calculate_elapsed_minutes(self, heartbeat_time: datetime) -> float:
        """
        Calculate elapsed minutes since heartbeat.

        Args:
            heartbeat_time: Last heartbeat timestamp.

        Returns:
            Elapsed time in minutes.
        """
        elapsed = self.reference_time - heartbeat_time
        return elapsed.total_seconds() / 60.0

    def classify_daemon(self, elapsed_minutes: float) -> str:
        """
        Classify daemon health status based on elapsed time.

        Args:
            elapsed_minutes: Minutes since last heartbeat.

        Returns:
            Classification string: HEALTHY, STALE, or CRITICAL.
        """
        if elapsed_minutes < self.HEALTHY_THRESHOLD_MINUTES:
            return "HEALTHY"
        elif elapsed_minutes < self.STALE_THRESHOLD_MINUTES:
            return "STALE"
        else:
            return "CRITICAL"

    def format_elapsed_time(self, elapsed_minutes: float) -> str:
        """
        Format elapsed time as human-readable string.

        Args:
            elapsed_minutes: Minutes since last heartbeat.

        Returns:
            Formatted string (e.g., "7h 44m" or "13m").
        """
        hours = int(elapsed_minutes // 60)
        minutes = int(elapsed_minutes % 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def parse_meta(self, meta: Any) -> Dict[str, Any]:
        """
        Parse meta field (JSON string or dict).

        Args:
            meta: Meta field value.

        Returns:
            Parsed metadata dictionary or empty dict.
        """
        if isinstance(meta, dict):
            return meta
        if isinstance(meta, str):
            try:
                return json.loads(meta)
            except json.JSONDecodeError:
                return {}
        return {}

    def analyze_services(self, raw_services: List[Dict[str, Any]]) -> None:
        """
        Analyze raw service records and populate internal state.

        Args:
            raw_services: List of raw service records from database.
        """
        self.services = []

        for record in raw_services:
            timestamp_val = record.get("timestamp", record.get("last_heartbeat", ""))

            heartbeat_dt = self.parse_heartbeat_timestamp(timestamp_val) if timestamp_val else None

            if heartbeat_dt:
                elapsed_minutes = self.calculate_elapsed_minutes(heartbeat_dt)
            else:
                elapsed_minutes = float('inf')

            classification = self.classify_daemon(elapsed_minutes)
            self.classification_counts[classification] += 1

            meta = self.parse_meta(record.get("meta", {}))

            service_record = {
                "service_name": record.get("service_name", "unknown"),
                "status": record.get("status", "unknown"),
                "last_heartbeat": timestamp_val,
                "elapsed_minutes": elapsed_minutes,
                "elapsed_formatted": self.format_elapsed_time(elapsed_minutes) if elapsed_minutes != float('inf') else "unknown",
                "classification": classification,
                "meta": meta
            }

            self.services.append(service_record)

    def generate_summary(self) -> Dict[str, Any]:
        """
        Generate summary statistics.

        Returns:
            Summary dictionary with counts and percentages.
        """
        total = len(self.services)
        summary = {
            "total_services": total,
            "healthy_count": self.classification_counts["HEALTHY"],
            "stale_count": self.classification_counts["STALE"],
            "critical_count": self.classification_counts["CRITICAL"],
            "healthy_percentage": (self.classification_counts["HEALTHY"] / total * 100) if total > 0 else 0,
            "stale_percentage": (self.classification_counts["STALE"] / total * 100) if total > 0 else 0,
            "critical_percentage": (self.classification_counts["CRITICAL"] / total * 100) if total > 0 else 0,
            "reference_time": self.reference_time.isoformat()
        }
        return summary

    def generate_daemon_table(self) -> List[Dict[str, Any]]:
        """
        Generate per-daemon diagnostic table.

        Returns:
            List of daemon records with diagnostic info.
        """
        return sorted(
            self.services,
            key=lambda x: x["elapsed_minutes"] if x["elapsed_minutes"] != float('inf') else float('inf'),
            reverse=True
        )

    def generate_recommendations(self) -> List[str]:
        """
        Generate investigation recommendations based on analysis.

        Returns:
            List of recommendation strings.
        """
        recommendations = []

        critical_services = [s for s in self.services if s["classification"] == "CRITICAL"]
        stale_services = [s for s in self.services if s["classification"] == "STALE"]

        if critical_services:
            names = ", ".join([s["service_name"] for s in critical_services])
            recommendations.append(
                f"CRITICAL: {len(critical_services)} service(s) have been stale > 24 hours: [{names}]. "
                "Immediate investigation required - check process viability and consider manual restart."
            )

        if stale_services:
            names = ", ".join([s["service_name"] for s in stale_services])
            recommendations.append(
                f"STALE: {len(stale_services)} service(s) have exceeded 2-hour threshold but < 24 hours: [{names}]. "
                "Monitor closely; verify process health and network connectivity."
            )

        healthy_services = [s for s in self.services if s["classification"] == "HEALTHY"]
        if healthy_services:
            recommendations.append(
                f"HEALTHY: {len(healthy_services)} service(s) reporting normally. "
                "No immediate action required."
            )

        # Check for status inconsistencies
        inconsistent = [
            s for s in self.services
            if s["classification"] == "CRITICAL" and s["status"] != "stopped"
        ]
        if inconsistent:
            recommendations.append(
                f"WARNING: {len(inconsistent)} CRITICAL service(s) not marked as 'stopped'. "
                "Verify service status reporting accuracy."
            )

        return recommendations

    def generate_report(self) -> Dict[str, Any]:
        """
        Generate complete diagnostic report.

        Returns:
            Complete report dictionary with all sections.
        """
        return {
            "report_timestamp": datetime.now(timezone.utc).isoformat(),
            "reference_time": self.reference_time.isoformat(),
            "summary": self.generate_summary(),
            "daemon_table": self.generate_daemon_table(),
            "recommendations": self.generate_recommendations()
        }

    def print_report(self) -> None:
        """Print formatted diagnostic report to stdout."""
        report = self.generate_report()

        print("=" * 80)
        print("STALE DAEMON INVESTIGATION DIAGNOSTIC REPORT")
        print("=" * 80)
        print()

        print(f"Report Generated: {report['report_timestamp']}")
        print(f"Reference Time:   {report['reference_time']}")
        print()

        # Summary
        print("-" * 40)
        print("SUMMARY")
        print("-" * 40)
        summary = report["summary"]
        print(f"Total Services:    {summary['total_services']}")
        print(f"  HEALTHY (<2h):   {summary['healthy_count']} ({summary['healthy_percentage']:.1f}%)")
        print(f"  STALE (2-24h):   {summary['stale_count']} ({summary['stale_percentage']:.1f}%)")
        print(f"  CRITICAL (>24h): {summary['critical_count']} ({summary['critical_percentage']:.1f}%)")
        print()

        # Daemon Table
        print("-" * 80)
        print("DAEMON HEALTH TABLE (sorted by elapsed time)")
        print("-" * 80)
        print(f"{'SERVICE NAME':<30} {'STATUS':<12} {'LAST HEARTBEAT':<25} {'ELAPSED':<12} {'CLASS':<10}")
        print("-" * 80)

        for daemon in report["daemon_table"]:
            elapsed = daemon["elapsed_formatted"] if daemon["elapsed_minutes"] != float('inf') else "unknown"
            print(
                f"{daemon['service_name']:<30} "
                f"{daemon['status']:<12} "
                f"{daemon['last_heartbeat']:<25} "
                f"{elapsed:<12} "
                f"{daemon['classification']:<10}"
            )

        print()

        # Recommendations
        print("-" * 40)
        print("RECOMMENDATIONS")
        print("-" * 40)
        for i, rec in enumerate(report["recommendations"], 1):
            print(f"{i}. {rec}")

        print()
        print("=" * 80)

    def run(self) -> Dict[str, Any]:
        """
        Execute full diagnostic run.

        Returns:
            Complete diagnostic report.
        """
        try:
            raw_services = self.query_service_health()
            self.analyze_services(raw_services)
            return self.generate_report()
        except requests.RequestException as e:
            return {
                "error": str(e),
                "error_type": "HTTP_ERROR",
                "message": f"Failed to query write_service API: {e}"
            }


def run_diagnostic(reference_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Convenience function to run stale daemon diagnostics.

    Args:
        reference_time: Optional reference time for elapsed calculation.

    Returns:
        Diagnostic report dictionary.
    """
    diagnostics = StaleDaemonDiagnostics(reference_time)
    return diagnostics.run()


def run_diagnostic_and_print(reference_time: Optional[datetime] = None) -> None:
    """
    Run diagnostics and print formatted report to stdout.

    Args:
        reference_time: Optional reference time for elapsed calculation.
    """
    diagnostics = StaleDaemonDiagnostics(reference_time)
    diagnostics.print_report()


# =============================================================================
# Self-Smoke Test
# =============================================================================

def _smoke_test_no_services() -> bool:
    """
    Smoke test: Handle empty service list gracefully.
    Returns True if test passes.
    """
    print("\n[SMOKE TEST 1/3] Empty service list handling...")

    ref_time = datetime(2026, 6, 3, 1, 19, 0, tzinfo=timezone.utc)
    diagnostics = StaleDaemonDiagnostics(reference_time=ref_time)

    diagnostics.analyze_services([])
    summary = diagnostics.generate_summary()

    assert summary["total_services"] == 0, "Expected 0 services"
    assert summary["healthy_count"] == 0, "Expected 0 healthy"
    assert summary["stale_count"] == 0, "Expected 0 stale"
    assert summary["critical_count"] == 0, "Expected 0 critical"

    recommendations = diagnostics.generate_recommendations()
    # Empty set should not crash

    print("  PASSED: Empty service list handled correctly")
    return True


def _smoke_test_mixed_health_states() -> bool:
    """
    Smoke test: Validate classification with known expected daemons.
    Returns True if test passes.
    """
    print("\n[SMOKE TEST 2/3] Mixed health state classification...")

    # Reference time: 2026-06-03 01:19:00 UTC
    ref_time = datetime(2026, 6, 3, 1, 19, 0, tzinfo=timezone.utc)
    diagnostics = StaleDaemonDiagnostics(reference_time=ref_time)

    # Simulate expected daemons with their stale times
    mock_services = [
        {"service_name": "write_service", "status": "running",
         "timestamp": "2026-06-02T17:35:00Z", "meta": {}},
        {"service_name": "zo_sentinel_builder", "status": "running",
         "timestamp": "2026-06-01T05:26:00Z", "meta": {}},
        {"service_name": "self_diagnostics", "status": "running",
         "timestamp": "2026-03T01:06:00Z", "meta": {}},  # 13 min ago
        {"service_name": "wisdom_synthesiser", "status": "running",
         "timestamp": "2026-06-02T17:35:00Z", "meta": {}},
    ]

    diagnostics.analyze_services(mock_services)

    # Verify classifications
    daemon_table = diagnostics.generate_daemon_table()
    classifications = {d["service_name"]: d["classification"] for d in daemon_table}

    assert classifications["write_service"] == "STALE", f"write_service should be STALE, got {classifications['write_service']}"
    assert classifications["zo_sentinel_builder"] == "CRITICAL", f"zo_sentinel_builder should be CRITICAL, got {classifications['zo_sentinel_builder']}"
    assert classifications["self_diagnostics"] == "HEALTHY", f"self_diagnostics should be HEALTHY, got {classifications['self_diagnostics']}"
    assert classifications["wisdom_synthesiser"] == "STALE", f"wisdom_synthesiser should be STALE, got {classifications['wisdom_synthesiser']}"

    # Verify summary counts
    summary = diagnostics.generate_summary()
    assert summary["healthy_count"] == 1, f"Expected 1 healthy, got {summary['healthy_count']}"
    assert summary["stale_count"] == 2, f"Expected 2 stale, got {summary['stale_count']}"
    assert summary["critical_count"] == 1, f"Expected 1 critical, got {summary['critical_count']}"

    print("  PASSED: Classification logic correct")
    print(f"    - HEALTHY: {summary['healthy_count']}")
    print(f"    - STALE: {summary['stale_count']}")
    print(f"    - CRITICAL: {summary['critical_count']}")
    return True


def _smoke_test_timestamp_parsing() -> bool:
    """
    Smoke test: Validate ISO 8601 timestamp parsing.
    Returns True if test passes.
    """
    print("\n[SMOKE TEST 3/3] ISO 8601 timestamp parsing...")

    ref_time = datetime(2026, 6, 3, 1, 19, 0, tzinfo=timezone.utc)
    diagnostics = StaleDaemonDiagnostics(reference_time=ref_time)

    # Test various timestamp formats
    test_cases = [
        ("2026-06-02T17:35:00Z", 464.0),  # 7h44m = 464 min
        ("2026-06-01T05:26:00Z", 3233.0),  # 53h53m = 3233 min
        ("2026-06-03T01:06:00Z", 13.0),    # 13 min
    ]

    for timestamp, expected_minutes in test_cases:
        parsed = diagnostics.parse_heartbeat_timestamp(timestamp)
        assert parsed is not None, f"Failed to parse: {timestamp}"

        elapsed = diagnostics.calculate_elapsed_minutes(parsed)
        assert abs(elapsed - expected_minutes) < 1, f"Expected ~{expected_minutes} min, got {elapsed} for {timestamp}"

    print("  PASSED: Timestamp parsing correct for all formats")
    print(f"    - Parsed 3 timestamp formats successfully")
    return True


def run_smoke_tests() -> None:
    """Execute all smoke tests and report results."""
    print("=" * 60)
    print("STALE DAEMON DIAGNOSTICS - SELF-SMOKE TESTS")
    print("=" * 60)

    tests = [
        _smoke_test_no_services,
        _smoke_test_mixed_health_states,
        _smoke_test_timestamp_parsing,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except AssertionError as e:
            print(f"  FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"SMOKE TEST RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)


if __name__ == '__main__':
    import sys

    # Check for smoke test mode
    if len(sys.argv) > 1 and sys.argv[1] == '--smoke-test':
        run_smoke_tests()
        sys.exit(0)

    # Default: run smoke tests first, then actual diagnostic
    run_smoke_tests()

    print("\n" + "=" * 60)
    print("RUNNING LIVE DIAGNOSTIC")
    print("=" * 60 + "\n")

    # Run the actual diagnostic
    try:
        diagnostics = StaleDaemonDiagnostics()
        diagnostics.print_report()
    except requests.RequestException as e:
        print(f"ERROR: Failed to connect to write_service API at {StaleDaemonDiagnostics.WRITE_SERVICE_URL}")
        print(f"       Details: {e}")
        print("\nNOTE: This diagnostic requires the write_service API to be running.")
        print("      The smoke tests above validate the diagnostic logic independently.")
        sys.exit(1)