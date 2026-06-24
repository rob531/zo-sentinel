import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DiagnosticReport:
    def __init__(self):
        self.report = {
            "timestamp": datetime.now().isoformat(),
            "findings": [],
            "recommendations": []
        }

    def add_finding(self, finding: str):
        self.report["findings"].append(finding)

    def add_recommendation(self, recommendation: str):
        self.report["recommendations"].append(recommendation)

    def generate_report(self) -> Dict[str, Any]:
        return self.report

def trace_write_path(mcp_ids: List[str], diagnostic_report: DiagnosticReport):
    # Step 1: Check for write_service timeout errors
    timeout_errors = check_write_service_timeouts(mcp_ids)
    if timeout_errors:
        diagnostic_report.add_finding("Write service timeout errors detected.")
        diagnostic_report.add_recommendation("Investigate and resolve write service timeouts.")

    # Step 2: Check for evidence_blob shape mismatches vs schema
    schema_mismatches = check_evidence_blob_schema_mismatches(mcp_ids)
    if schema_mismatches:
        diagnostic_report.add_finding("Evidence blob schema mismatches detected.")
        diagnostic_report.add_recommendation("Ensure evidence blobs conform to the expected schema.")

    # Step 3: Check for filtering conditions that drop rows
    filtering_conditions = check_filtering_conditions(mcp_ids)
    if filtering_conditions:
        diagnostic_report.add_finding("Filtering conditions dropping rows detected.")
        diagnostic_report.add_recommendation("Review and adjust filtering conditions to retain all rows.")

    # Step 4: Check for batch size or batching logic bugs
    batching_issues = check_batching_logic(mcp_ids)
    if batching_issues:
        diagnostic_report.add_finding("Batch size or batching logic bugs detected.")
        diagnostic_report.add_recommendation("Review and fix batch size or batching logic.")

def check_write_service_timeouts(mcp_ids: List[str]) -> bool:
    # Query write_service for timeout errors
    query = """
    SELECT COUNT(*)
    FROM write_service_errors
    WHERE error_type = 'timeout'
    AND mcp_id IN ({})
    AND timestamp >= NOW() - INTERVAL '1 hour'
    """.format(','.join(['%s'] * len(mcp_ids)))

    try:
        cursor.execute(query, mcp_ids)
        timeout_count = cursor.fetchone()[0]
        return timeout_count > 0
    except Exception as e:
        logger.error(f"Error querying write service timeouts: {e}")
        return False

def check_evidence_blob_schema_mismatches(mcp_ids: List[str]) -> bool:
    # Query write_service for evidence_blob schema mismatches
    query = """
    SELECT COUNT(*)
    FROM write_service_errors
    WHERE error_type = 'schema_mismatch'
    AND mcp_id IN ({})
    AND timestamp >= NOW() - INTERVAL '1 hour'
    """.format(','.join(['%s'] * len(mcp_ids)))

    try:
        cursor.execute(query, mcp_ids)
        mismatch_count = cursor.fetchone()[0]
        return mismatch_count > 0
    except Exception as e:
        logger.error(f"Error querying evidence blob schema mismatches: {e}")
        return False

def check_filtering_conditions(mcp_ids: List[str]) -> bool:
    # Query write_service for filtering conditions that drop rows
    query = """
    SELECT COUNT(*)
    FROM write_service_errors
    WHERE error_type = 'filtering_condition'
    AND mcp_id IN ({})
    AND timestamp >= NOW() - INTERVAL '1 hour'
    """.format(','.join(['%s'] * len(mcp_ids)))

    try:
        cursor.execute(query, mcp_ids)
        filtering_count = cursor.fetchone()[0]
        return filtering_count > 0
    except Exception as e:
        logger.error(f"Error querying filtering conditions: {e}")
        return False

def check_batching_logic(mcp_ids: List[str]) -> bool:
    # Query write_service for batch size or batching logic bugs
    query = """
    SELECT COUNT(*)
    FROM write_service_errors
    WHERE error_type = 'batching_logic'
    AND mcp_id IN ({})
    AND timestamp >= NOW() - INTERVAL '1 hour'
    """.format(','.join(['%s'] * len(mcp_ids)))

    try:
        cursor.execute(query, mcp_ids)
        batching_count = cursor.fetchone()[0]
        return batching_count > 0
    except Exception as e:
        logger.error(f"Error querying batching logic: {e}")
        return False

# Example usage
if __name__ == "__main__":
    # Sample MCP IDs
    mcp_ids = ["mcp1", "mcp2", "mcp3"]  # Replace with actual MCP IDs

    # Initialize diagnostic report
    diagnostic_report = DiagnosticReport()

    # Trace the write path
    trace_write_path(mcp_ids, diagnostic_report)

    # Generate and print the diagnostic report
    report = diagnostic_report.generate_report()
    print(report)