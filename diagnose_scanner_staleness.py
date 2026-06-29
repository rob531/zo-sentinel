import logging
from datetime import datetime, timedelta
from typing import Optional

from zo_sentinel.services import service_health
from zo_sentinel.scanners.mcp_scanner import MCPScanner
from zo_sentinel.services.write_service import WriteService

def diagnose_scanner_staleness() -> str:
    """Diagnose why MCPScanner is stale and return root cause."""
    logger = logging.getLogger(__name__)

    # Check last heartbeat
    last_heartbeat = service_health.get_last_heartbeat("mcp_scanner")
    if last_heartbeat is None:
        return "Root Cause: No heartbeat record found for mcp_scanner"

    stale_threshold = timedelta(hours=4)
    if datetime.now() - last_heartbeat < stale_threshold:
        return "Root Cause: Scanner is not actually stale (heartbeat within threshold)"

    # Check scanner's error handling and loop pattern
    scanner = MCPScanner()
    try:
        # Verify scanner can connect to write_service
        write_service = WriteService()
        if not write_service.is_connected():
            return "Root Cause: Scanner cannot connect to write_service (network/config issue)"

        # Check scanner's main loop status
        if not scanner.is_running():
            return "Root Cause: Scanner's main loop has stopped (exception or config issue)"

        # Check for recent errors in scanner logs
        recent_errors = scanner.get_recent_errors(stale_threshold)
        if recent_errors:
            return f"Root Cause: Scanner encountered errors: {recent_errors}"

        # Check for timeouts
        if scanner.has_timeout_issues():
            return "Root Cause: Scanner is experiencing timeout issues"

    except Exception as e:
        logger.error(f"Diagnostic error: {str(e)}")
        return f"Root Cause: Diagnostic failed with error: {str(e)}"

    return "Root Cause: Unknown (no clear issue detected)"

if __name__ == "__main__":
    print(diagnose_scanner_staleness())