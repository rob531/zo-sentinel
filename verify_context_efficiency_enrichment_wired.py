import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("verify_context_efficiency_enrichment_wired")

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"


def query_table(table: str, conditions: str = None, columns: str = "*") -> list:
    """Query data from write service."""
    payload = {
        "table": table,
        "query": {
            "columns": columns,
            "conditions": conditions
        }
    }
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except Exception as e:
        logger.warning(f"Query failed for {table}: {e}")
        return []


def check_context_efficiency_entries() -> dict:
    """Check for context_efficiency entries in signal enrichments."""
    query = query_table(
        "mcp_signal_enrichments",
        conditions="signal_type='context_efficiency'",
        columns="id,signal_id,enrichment_data,created_at"
    )
    return {
        "count": len(query),
        "entries": query[:5]  # Sample of entries
    }


def check_signal_analyser_wiring() -> dict:
    """Check if signal_analyser processes context_efficiency."""
    try:
        file_path = PROJECT_ROOT / "signal_analyser.py"
        if file_path.exists():
            content = file_path.read_text()
            has_reference = "context_efficiency" in content or "context_efficiency_enrichment" in content
            return {"found": file_path.exists(), "references_context_efficiency": has_reference}
    except Exception as e:
        logger.warning(f"Could not check signal_analyser.py: {e}")
    return {"found": False, "references_context_efficiency": False}


def check_trust_synthesiser_wiring() -> dict:
    """Check if trust_synthesiser reads context_efficiency entries."""
    try:
        file_path = PROJECT_ROOT / "trust_synthesiser.py"
        if file_path.exists():
            content = file_path.read_text()
            has_query = "mcp_signal_enrichments" in content
            has_filter = "context_efficiency" in content
            return {
                "found": file_path.exists(),
                "queries_enrichments": has_query,
                "filters_context_efficiency": has_filter
            }
    except Exception as e:
        logger.warning(f"Could not check trust_synthesiser.py: {e}")
    return {"found": False, "queries_enrichments": False, "filters_context_efficiency": False}


def generate_diagnostic_blob(status: str, enrich_check: dict, signal_analyser: dict, trust_synthesiser: dict) -> dict:
    """Generate diagnostic blob for output."""
    return {
        "verification": "context_efficiency_enrichment_wiring",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "diagnostics": {
            "enrichment_entries": enrich_check,
            "signal_analyser_wiring": signal_analyser,
            "trust_synthesiser_wiring": trust_synthesiser,
            "pipeline_healthy": (
                enrich_check["count"] > 0 and
                trust_synthesiser.get("queries_enrichments", False)
            )
        },
        "recommendations": []
    }


def add_recommendations(diagnostic: dict, enrich_count: int, trust_wired: dict):
    """Add recommendations based on findings."""
    if enrich_count == 0:
        diagnostic["recommendations"].append({
            "severity": "HIGH",
            "issue": "No context_efficiency entries found in mcp_signal_enrichments",
            "action": "Ensure context_efficiency_enrichment.py is running and producing enrichments"
        })
    if not trust_wired.get("queries_enrichments", False):
        diagnostic["recommendations"].append({
            "severity": "HIGH",
            "issue": "trust_synthesiser.py does not query mcp_signal_enrichments",
            "action": "Add query for signal_type='context_efficiency' in trust_synthesiser"
        })
    if not trust_wired.get("filters_context_efficiency", False):
        diagnostic["recommendations"].append({
            "severity": "MEDIUM",
            "issue": "trust_synthesiser.py does not filter for context_efficiency",
            "action": "Add WHERE clause for context_efficiency signal type"
        })


def main():
    """Main verification logic."""
    logger.info("Starting context_efficiency enrichment wiring verification")

    # Check enrichment entries
    enrich_check = check_context_efficiency_entries()
    enrich_count = enrich_check["count"]
    logger.info(f"Found {enrich_count} context_efficiency entries in mcp_signal_enrichments")

    # Check pipeline wiring
    signal_analyser = check_signal_analyser_wiring()
    trust_synthesiser = check_trust_synthesiser_wiring()

    logger.info(f"signal_analyser wiring: {signal_analyser}")
    logger.info(f"trust_synthesiser wiring: {trust_synthesiser}")

    # Determine status
    if enrich_count == 0:
        status = "NOT_WIRED"
        logger.warning("context_efficiency enrichment is NOT wired - no entries found")
    elif trust_synthesiser.get("queries_enrichments", False):
        status = "WIRED"
        logger.info("context_efficiency enrichment is properly wired through trust_synthesiser")
    else:
        status = "PARTIALLY_WIRED"
        logger.warning("context_efficiency enrichment entries exist but trust_synthesiser not reading them")

    # Generate diagnostic blob
    diagnostic = generate_diagnostic_blob(status, enrich_check, signal_analyser, trust_synthesiser)
    add_recommendations(diagnostic, enrich_count, trust_synthesiser)

    # Output result
    print("\n" + "="*60)
    print("CONTEXT EFFICIENCY ENRICHMENT WIRING VERIFICATION")
    print("="*60)
    print(json.dumps(diagnostic, indent=2))
    print("="*60)

    # Heartbeat
    try:
        heartbeat_payload = {
            "table": "service_health",
            "rows": {
                "service": "verify_context_efficiency_enrichment_wired",
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": status
            }
        }
        requests.post(WRITE_SERVICE_URL, json=heartbeat_payload, timeout=5)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")

    return 0 if status == "WIRED" else 1


if __name__ == "__main__":
    import requests
    sys.exit(main())