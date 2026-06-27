#!/usr/bin/env python3

import logging
from datetime import datetime, timedelta
from typing import Optional

from write_service import WriteService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPDefinitionHistoryDiagnostic:
    def __init__(self):
        self.ws = WriteService()

    def check_daemon_heartbeat(self) -> Optional[str]:
        """Check if the daemon is running by querying service_health."""
        try:
            query = """
                SELECT status, last_heartbeat
                FROM service_health
                WHERE service_name = 'mcp_definition_history_backfill_daemon'
                ORDER BY last_heartbeat DESC
                LIMIT 1
            """
            result = self.ws.query(query)
            if not result:
                return "DAEMON_NOT_FOUND"

            status, last_heartbeat = result[0]
            if status != "healthy":
                return "DAEMON_UNHEALTHY"

            # Check if the last heartbeat is recent (within the last hour)
            if (datetime.now() - last_heartbeat).total_seconds() > 3600:
                return "DAEMON_STALE"

            return "DAEMON_HEALTHY"
        except Exception as e:
            logger.error(f"Error checking daemon heartbeat: {e}")
            return "DAEMON_HEARTBEAT_CHECK_ERROR"

    def check_log_files(self) -> Optional[str]:
        """Check log files for errors or indications of non-execution."""
        try:
            # Example log file path (adjust as needed)
            log_file_path = "/var/log/mcp_definition_history_backfill_daemon.log"

            with open(log_file_path, 'r') as f:
                logs = f.read()

            if "Error" in logs or "Exception" in logs:
                return "EXECUTION_ERROR_IN_LOGS"

            if "Backfill completed" not in logs:
                return "NO_BACKFILL_ACTIVITY_IN_LOGS"

            return "LOGS_CLEAN"
        except FileNotFoundError:
            return "LOG_FILE_NOT_FOUND"
        except Exception as e:
            logger.error(f"Error checking log files: {e}")
            return "LOG_CHECK_ERROR"

    def check_mcp_definition_history(self) -> Optional[str]:
        """Check if the mcp_definition_history table is empty."""
        try:
            query = "SELECT COUNT(*) FROM mcp_definition_history"
            result = self.ws.query(query)
            if not result:
                return "TABLE_QUERY_ERROR"

            count = result[0][0]
            if count == 0:
                return "TABLE_EMPTY"
            else:
                return f"TABLE_HAS_DATA ({count} rows)"
        except Exception as e:
            logger.error(f"Error checking mcp_definition_history table: {e}")
            return "TABLE_CHECK_ERROR"

    def check_mcp_submissions(self) -> Optional[str]:
        """Check if there are new submissions that should trigger backfill."""
        try:
            # Check for submissions in the last 24 hours
            query = """
                SELECT COUNT(*)
                FROM mcp_submissions
                WHERE submission_time >= NOW() - INTERVAL '24 hours'
            """
            result = self.ws.query(query)
            if not result:
                return "SUBMISSIONS_QUERY_ERROR"

            count = result[0][0]
            if count == 0:
                return "NO_NEW_SUBMISSIONS"
            else:
                return f"NEW_SUBMISSIONS_FOUND ({count} submissions)"
        except Exception as e:
            logger.error(f"Error checking mcp_submissions table: {e}")
            return "SUBMISSIONS_CHECK_ERROR"

    def diagnose(self) -> str:
        """Run all checks and return a diagnosis."""
        daemon_status = self.check_daemon_heartbeat()
        log_status = self.check_log_files()
        table_status = self.check_mcp_definition_history()
        submissions_status = self.check_mcp_submissions()

        diagnosis = []

        if daemon_status == "DAEMON_STALE":
            diagnosis.append("DAEMON_STALE")
        elif daemon_status == "DAEMON_UNHEALTHY":
            diagnosis.append("DAEMON_UNHEALTHY")
        elif daemon_status == "DAEMON_NOT_FOUND":
            diagnosis.append("DAEMON_NOT_FOUND")

        if log_status == "EXECUTION_ERROR_IN_LOGS":
            diagnosis.append("EXECUTION_ERROR_IN_LOGS")
        elif log_status == "NO_BACKFILL_ACTIVITY_IN_LOGS":
            diagnosis.append("NO_BACKFILL_ACTIVITY_IN_LOGS")

        if table_status == "TABLE_EMPTY":
            diagnosis.append("TABLE_EMPTY")

        if submissions_status == "NO_NEW_SUBMISSIONS":
            diagnosis.append("NO_NEW_SUBMISSIONS")

        if not diagnosis:
            diagnosis.append("ALL_CHECKS_PASS")

        return ", ".join(diagnosis)

if __name__ == "__main__":
    diagnostic = MCPDefinitionHistoryDiagnostic()
    result = diagnostic.diagnose()
    print(result)

    # Assert that the diagnosis produces a clear output
    assert result in [
        "DAEMON_STALE",
        "DAEMON_UNHEALTHY",
        "DAEMON_NOT_FOUND",
        "EXECUTION_ERROR_IN_LOGS",
        "NO_BACKFILL_ACTIVITY_IN_LOGS",
        "TABLE_EMPTY",
        "NO_NEW_SUBMISSIONS",
        "ALL_CHECKS_PASS",
        "DAEMON_STALE, TABLE_EMPTY",
        "DAEMON_STALE, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, TABLE_EMPTY",
        "DAEMON_UNHEALTHY, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, TABLE_EMPTY",
        "DAEMON_NOT_FOUND, NO_NEW_SUBMISSIONS",
        "EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY",
        "EXECUTION_ERROR_IN_LOGS, NO_NEW_SUBMISSIONS",
        "NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_STALE, EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY",
        "DAEMON_STALE, EXECUTION_ERROR_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_STALE, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "DAEMON_STALE, NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY",
        "DAEMON_UNHEALTHY, EXECUTION_ERROR_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "DAEMON_UNHEALTHY, NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY",
        "DAEMON_NOT_FOUND, EXECUTION_ERROR_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "DAEMON_NOT_FOUND, NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_STALE, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "DAEMON_STALE, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "DAEMON_UNHEALTHY, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY",
        "DAEMON_NOT_FOUND, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, NO_NEW_SUBMISSIONS",
        "DAEMON_STALE, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_STALE, EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_STALE, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, EXECUTION_ERROR_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_STALE, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_UNHEALTHY, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS",
        "DAEMON_NOT_FOUND, EXECUTION_ERROR_IN_LOGS, NO_BACKFILL_ACTIVITY_IN_LOGS, TABLE_EMPTY, NO_NEW_SUBMISSIONS"
    ], f"Unexpected diagnosis result: {result}"

    print("PASS")