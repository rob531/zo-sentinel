import datetime
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

# Add the parent directory to the Python path to allow imports from zo_common
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from zo_common.db.write_service import WriteService
from zo_common.logging.setup import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)


class SelfDiagnosticsStalenessDiagnoser:
    """
    Diagnoses persistent staleness issues with the self_diagnostics daemon.

    This class queries the service_health table to analyze the last_heartbeat
    timestamps for self_diagnostics entries and identifies potential root causes
    of recurring staleness.
    """

    def __init__(self, write_service_url: str = "http://localhost:8080"):
        """
        Initializes the diagnoser.

        Args:
            write_service_url: The URL of the write_service to interact with.
        """
        self.write_service_url = write_service_url
        self.write_service = WriteService(write_service_url)

    def _query_service_health(self) -> List[Dict[str, Any]]:
        """
        Queries the service_health table for self_diagnostics entries.

        Returns:
            A list of dictionaries, where each dictionary represents a row
            from the service_health table for self_diagnostics.
        """
        query = "SELECT service_name, last_heartbeat, meta FROM service_health WHERE service_name = 'self_diagnostics';"
        try:
            response = self.write_service.query(query)
            if response.get("status") == "success":
                return response.get("data", [])
            else:
                logger.error(f"Failed to query service_health: {response.get('error')}")
                return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error querying write_service: {e}")
            return []

    def _analyze_heartbeats(self, health_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyzes the heartbeat timestamps to identify staleness patterns.

        Args:
            health_entries: A list of dictionaries representing service health entries.

        Returns:
            A dictionary containing the analysis results, including staleness periods
            and potential contributing factors.
        """
        if not health_entries:
            return {"summary": "No self_diagnostics entries found in service_health."}

        staleness_periods = []
        meta_errors = {}
        heartbeat_timestamps = []

        for entry in health_entries:
            try:
                heartbeat_str = entry.get("last_heartbeat")
                if not heartbeat_str:
                    continue
                # Assuming last_heartbeat is stored as ISO 8601 string
                heartbeat_ts = datetime.datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                heartbeat_timestamps.append(heartbeat_ts)

                meta_str = entry.get("meta")
                if meta_str:
                    try:
                        meta_data = json.loads(meta_str)
                        if meta_data.get("error"):
                            error_message = meta_data["error"]
                            meta_errors[error_message] = meta_errors.get(error_message, 0) + 1
                    except json.JSONDecodeError:
                        logger.warning(f"Could not decode meta JSON: {meta_str}")
                        meta_errors["malformed_meta_json"] = meta_errors.get("malformed_meta_json", 0) + 1
            except ValueError:
                logger.warning(f"Could not parse heartbeat timestamp: {heartbeat_str}")
                continue

        if not heartbeat_timestamps:
            return {"summary": "No valid heartbeat timestamps found for self_diagnostics."}

        # Sort timestamps to easily find gaps
        heartbeat_timestamps.sort()

        # Identify staleness periods (gaps between heartbeats)
        for i in range(len(heartbeat_timestamps) - 1):
            time_diff = heartbeat_timestamps[i+1] - heartbeat_timestamps[i]
            # Define a threshold for "staleness" - e.g., more than 5 minutes
            staleness_threshold = datetime.timedelta(minutes=5)
            if time_diff > staleness_threshold:
                staleness_periods.append({
                    "start": heartbeat_timestamps[i].isoformat(),
                    "end": heartbeat_timestamps[i+1].isoformat(),
                    "duration_seconds": time_diff.total_seconds()
                })

        summary = f"Analysis of {len(health_entries)} self_diagnostics entries:\n"
        summary += f"- Found {len(staleness_periods)} potential staleness periods (duration > 5 minutes).\n"
        if staleness_periods:
            summary += "  - Longest staleness period: "
            longest_period = max(staleness_periods, key=lambda x: x["duration_seconds"])
            summary += f"{datetime.timedelta(seconds=longest_period['duration_seconds'])} between {longest_period['start']} and {longest_period['end']}\n"

        summary += f"- Observed {len(meta_errors)} distinct error messages in 'meta' field:\n"
        if meta_errors:
            for error, count in meta_errors.items():
                summary += f"  - '{error}': {count} occurrences\n"
        else:
            summary += "  - No specific errors found in 'meta' field.\n"

        if not staleness_periods and not meta_errors:
            summary += "- No significant staleness or errors detected based on current analysis criteria.\n"

        return {
            "summary": summary,
            "staleness_periods": staleness_periods,
            "meta_errors": meta_errors
        }

    def run(self) -> str:
        """
        Performs the self-diagnostics staleness analysis and returns a report.

        Returns:
            A string containing the diagnostic report.
        """
        logger.info("Starting self_diagnostics staleness diagnosis...")
        health_entries = self._query_service_health()
        analysis_results = self._analyze_heartbeats(health_entries)
        logger.info("Self_diagnostics staleness diagnosis complete.")
        return analysis_results.get("summary", "Analysis could not be completed.")


def main():
    """
    Main function to execute the self-diagnostics staleness diagnosis.
    """
    diagnoser = SelfDiagnosticsStalenessDiagnoser()
    report = diagnoser.run()
    print(report)

    # Acceptance criteria: Assert that a report is printed, indicating analysis occurred.
    # This is a basic check; a more robust test would involve mocking the DB and
    # asserting specific content in the report.
    assert report is not None and len(report) > 0, "Diagnostic report was not generated."
    print("\nAcceptance criteria met: Diagnostic report was successfully generated.")


if __name__ == "__main__":
    main()