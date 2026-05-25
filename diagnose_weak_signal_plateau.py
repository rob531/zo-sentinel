import logging
import sys
import time
import json
from datetime import datetime
from typing import Any

import requests

LOG = logging.getLogger("diagnose_weak_signal_plateau")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SERVICE_NAME = "diagnose_weak_signal_plateau"
SIGNAL_NAME = "diagnostic"
VERSION = "1.0"

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

SCORE_FIELDS = [
    "permission_scope",
    "temporal_stability", 
    "tool_description_safety"
]

ENRICHMENT_MODULE_MAP = {
    "permission_scope": "permission_scope_enrichment_v2",
    "temporal_stability": "temporal_stability_enrichment_v2",
    "tool_description_safety": "tool_description_safety_v2"
}

METADATA_FIELDS_TO_CHECK = [
    "metadata_raw",
    "raw_metadata",
    "metadata_json",
    "enrichment_data",
    "score_metadata",
    "safety_metadata"
]


def send_heartbeat():
    """Send service heartbeat."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        LOG.warning(f"Heartbeat failed: {e}")


def query_enrichments() -> list[dict]:
    """Query mcp_signal_enrichments table."""
    try:
        payload = {
            "table": "mcp_signal_enrichments",
            "action": "select",
            "wait": True
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "success":
            return result.get("data", [])
        else:
            LOG.error(f"Query failed: {result}")
            return []
    except Exception as e:
        LOG.error(f"Failed to query enrichments: {e}")
        return []


def analyze_score_field(enrichments: list[dict], field: str) -> dict:
    """Analyze a specific score field for distinct values and source."""
    values = []
    wrong_field_count = 0
    correct_field_count = 0
    null_count = 0
    
    for row in enrichments:
        raw_metadata = row.get("raw_metadata", {})
        enrichment_data = row.get("enrichment_data", {})
        
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata)
            except:
                pass
        
        direct_value = row.get(field)
        metadata_value = raw_metadata.get(field) if isinstance(raw_metadata, dict) else None
        enrichment_value = enrichment_data.get(field) if isinstance(enrichment_data, dict) else None
        
        if direct_value is not None:
            values.append(direct_value)
            if isinstance(direct_value, (int, float)):
                correct_field_count += 1
        elif metadata_value is not None:
            values.append(metadata_value)
            wrong_field_count += 1
        elif enrichment_value is not None:
            values.append(enrichment_value)
            wrong_field_count += 1
        else:
            null_count += 1
    
    distinct = set(values) if values else set()
    
    return {
        "field": field,
        "distinct_count": len(distinct),
        "distinct_values": sorted(list(distinct))[:10],
        "total_rows": len(enrichments),
        "correct_field_hits": correct_field_count,
        "wrong_field_hits": wrong_field_count,
        "null_count": null_count,
        "is_flat": len(distinct) <= 3 and len(enrichments) > 10
    }


def check_enrichment_modules() -> dict:
    """Check which enrichment modules have run."""
    modules_status = {}
    for score_field, module_name in ENRICHMENT_MODULE_MAP.items():
        modules_status[score_field] = {
            "module_name": module_name,
            "exists": True
        }
    return modules_status


def generate_diagnosis(analyses: list[dict]) -> dict:
    """Generate diagnosis based on analysis."""
    findings = []
    
    for analysis in analyses:
        if analysis["is_flat"]:
            wrong_pct = analysis["wrong_field_hits"] / max(analysis["total_rows"], 1) * 100
            if wrong_pct > 50:
                findings.append({
                    "field": analysis["field"],
                    "severity": "HIGH",
                    "issue": f"Scores landing in wrong metadata field ({wrong_pct:.1f}% off-target)",
                    "distinct_values": analysis["distinct_values"],
                    "correct_hits": analysis["correct_field_hits"],
                    "wrong_hits": analysis["wrong_field_hits"]
                })
            elif analysis["null_count"] > analysis["total_rows"] * 0.8:
                findings.append({
                    "field": analysis["field"],
                    "severity": "MEDIUM",
                    "issue": "Enrichment module running but scores not persisting to enrichment table",
                    "null_count": analysis["null_count"],
                    "total_rows": analysis["total_rows"]
                })
            else:
                findings.append({
                    "field": analysis["field"],
                    "severity": "LOW",
                    "issue": f"Only {analysis['distinct_count']} distinct values (possible legitimate low variance)",
                    "distinct_values": analysis["distinct_values"]
                })
    
    return {
        "diagnosis_time": datetime.utcnow().isoformat(),
        "findings": findings,
        "summary": f"{len([f for f in findings if f['severity'] == 'HIGH'])} HIGH, {len([f for f in findings if f['severity'] == 'MEDIUM'])} MEDIUM, {len([f for f in findings if f['severity'] == 'LOW'])} LOW severity issues"
    }


def run():
    """Main diagnostic run."""
    LOG.info(f"Starting {SERVICE_NAME} diagnostic")
    
    send_heartbeat()
    
    enrichments = query_enrichments()
    if not enrichments:
        LOG.error("No enrichment data found in mcp_signal_enrichments")
        sys.exit(1)
    
    LOG.info(f"Retrieved {len(enrichments)} enrichment records")
    
    analyses = []
    for field in SCORE_FIELDS:
        analysis = analyze_score_field(enrichments, field)
        analyses.append(analysis)
        LOG.info(f"{field}: {analysis['distinct_count']} distinct values, "
                 f"correct={analysis['correct_field_hits']}, wrong={analysis['wrong_field_hits']}")
    
    diagnosis = generate_diagnosis(analyses)
    
    LOG.info(f"Diagnosis: {diagnosis['summary']}")
    for finding in diagnosis["findings"]:
        LOG.warning(f"  [{finding['severity']}] {finding['field']}: {finding['issue']}")
    
    try:
        payload = {
            "table": "diagnostic_results",
            "rows": {
                "service": SERVICE_NAME,
                "diagnosis_time": diagnosis["diagnosis_time"],
                "summary": diagnosis["summary"],
                "findings": json.dumps(diagnosis["findings"]),
                "analyses": json.dumps(analyses)
            },
            "wait": True
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            LOG.info("Diagnosis results written to diagnostic_results table")
    except Exception as e:
        LOG.error(f"Failed to write diagnosis results: {e}")
    
    send_heartbeat()
    LOG.info(f"Completed {SERVICE_NAME} diagnostic")


if __name__ == "__main__":
    run()