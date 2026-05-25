#!/usr/bin/env python3
"""
signal_v2_diversity_validator.py
Validates that v2 enrichment modules produce more than 3 distinct score values
when exercised against a synthetic corpus of 34 fingerprints.
"""

import logging
import random
import string
import time
from datetime import datetime, timezone
from typing import Any

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
ENRICHMENT_TABLE = "mcp_signal_enrichments"

# 34 synthetic fingerprint templates for diversity testing
SYNTHETIC_FINGERPRINTS = [
    f"fp_{''.join(random.choices(string.ascii_lowercase + string.digits, k=16))}"
    for _ in range(34)
]

# V2 enrichment modules to test
V2_MODULES = [
    "temporal_stability_enrichment_v2",
    "tool_description_safety_enrichment_v2",
    "permission_scope_enrichment_v2",
]


def write_via_service(table: str, rows: dict[str, Any]) -> dict:
    """Write records to service via HTTP POST."""
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Write service error: {e}")
        return {"error": str(e)}


def generate_synthetic_corpus() -> list[dict]:
    """Generate 34 synthetic fingerprints with varied metadata."""
    corpus = []
    for i, fp_id in enumerate(SYNTHETIC_FINGERPRINTS):
        corpus.append(
            {
                "fingerprint_id": fp_id,
                "server_name": f"test_server_{i % 5}",
                "tool_count": random.randint(1, 50),
                "permission_count": random.randint(0, 15),
                "description_length": random.randint(10, 500),
                "has_category": random.choice([True, False]),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return corpus


def insert_synthetic_fingerprints(corpus: list[dict]) -> bool:
    """Insert synthetic fingerprints into enrichment table."""
    rows = {"fingerprints": corpus}
    result = write_via_service("insert_fingerprints", rows)
    return "error" not in result


def run_enrichment_validation() -> dict[str, Any]:
    """Run v2 enrichment modules and validate diversity."""
    results = {}
    all_distinct_counts = []

    for module in V2_MODULES:
        logger.info(f"Testing enrichment module: {module}")
        
        # Trigger enrichment via write service
        payload = {
            "table": module,
            "rows": {
                "fingerprints": SYNTHETIC_FINGERPRINTS,
                "enrichment_type": "diversity_test",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "wait": True,
        }
        
        try:
            response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=60)
            response.raise_for_status()
            enrich_result = response.json()
        except requests.RequestException as e:
            logger.error(f"Enrichment error for {module}: {e}")
            enrich_result = {"error": str(e)}

        # Query distinct scores from mcp_signal_enrichments
        distinct_scores = query_distinct_scores(module)
        distinct_count = len(distinct_scores)
        
        results[module] = {
            "distinct_score_count": distinct_count,
            "distinct_scores": distinct_scores,
            "passed": distinct_count > 3,
            "enrichment_result": enrich_result,
        }
        all_distinct_counts.append(distinct_count)
        
        logger.info(
            f"{module}: {distinct_count} distinct scores "
            f"(passes threshold: {distinct_count > 3})"
        )

    # Overall validation
    overall_passed = all(cnt > 3 for cnt in all_distinct_counts)
    results["overall_validation"] = {
        "passed": overall_passed,
        "all_distinct_counts": all_distinct_counts,
        "min_distinct": min(all_distinct_counts) if all_distinct_counts else 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return results


def query_distinct_scores(module: str) -> list[float]:
    """Query distinct score values from enrichment table."""
    query_payload = {
        "table": "query_distinct_scores",
        "rows": {
            "enrichment_module": module,
            "limit": 100,
        },
        "wait": True,
    }
    
    try:
        response = requests.post(WRITE_SERVICE_URL, json=query_payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("distinct_scores", [])
    except requests.RequestException as e:
        logger.error(f"Query distinct scores error: {e}")
        return []


def log_validation_result(results: dict[str, Any]) -> None:
    """Log validation results to service_health."""
    heartbeat_payload = {
        "table": "service_health",
        "rows": {
            "service": "signal_v2_diversity_validator",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "validation_status": "passed" if results["overall_validation"]["passed"] else "failed",
            "min_distinct_scores": results["overall_validation"]["min_distinct"],
            "details": str(results),
        },
        "wait": True,
    }
    
    try:
        requests.post(WRITE_SERVICE_URL, json=heartbeat_payload, timeout=10)
    except requests.RequestException as e:
        logger.warning(f"Failed to log heartbeat: {e}")


def run() -> None:
    """Main execution loop for diversity validator."""
    logger.info("Starting signal_v2_diversity_validator")
    
    # Generate and insert synthetic corpus
    logger.info("Generating synthetic fingerprint corpus (34 fingerprints)")
    corpus = generate_synthetic_corpus()
    
    if not insert_synthetic_fingerprints(corpus):
        logger.error("Failed to insert synthetic fingerprints")
        return
    
    logger.info("Synthetic corpus inserted successfully")
    
    # Run validation cycles
    max_attempts = 3
    for attempt in range(max_attempts):
        logger.info(f"Diversity validation attempt {attempt + 1}/{max_attempts}")
        
        results = run_enrichment_validation()
        
        if results["overall_validation"]["passed"]:
            logger.info(
                f"Validation PASSED: All modules produced >3 distinct scores "
                f"(min: {results['overall_validation']['min_distinct']})"
            )
            log_validation_result(results)
            return
        
        logger.warning(
            f"Validation attempt {attempt + 1} failed, "
            f"min distinct scores: {results['overall_validation']['min_distinct']}"
        )
        
        if attempt < max_attempts - 1:
            time.sleep(5)
    
    # Final failure logging
    logger.error(
        f"Validation FAILED after {max_attempts} attempts: "
        f"modules did not produce >3 distinct scores"
    )
    log_validation_result(results)


if __name__ == "__main__":
    run()