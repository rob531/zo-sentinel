#!/usr/bin/env python3
"""
verify_attestation_engine_dynamic_evidence.py

Quality pass utility to verify attestation_engine.py includes dynamic evidence
language referencing injection_resilience results.

Queries mcp_attestations table via write_service :8772 for recent attestations.
Checks attestation text for dynamic evidence references.
Reports whether Phase 8 pi_scorer output is being cited in attestations
or if extension is needed per Phase 8 completion notes.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# deps: requests

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HTTP_TIMEOUT = 10
ATTESTATION_ENGINE_PATH = Path(__file__).parent / "attestation_engine.py"

# Dynamic evidence indicators to look for in attestation text
DYNAMIC_EVIDENCE_PATTERNS = {
    "pi_scorer_output": [
        r"pi_score",
        r"pi_scorer",
        r"pi_result",
        r"mcp_signal_scores",
        r"signal_scores",
    ],
    "injection_resilience": [
        r"injection_resilience",
        r"resilience_score",
        r"resilience_level",
        r"prompt_injection",
        r"pi_resilience",
    ],
    "enricher_output": [
        r"compute_score",
        r"enrichment_score",
        r"signal_score",
        r"quality_score",
        r"discrimination_score",
    ],
}


def ws_query(sql: str, params: list = None) -> list:
    """Execute SELECT via write_service /query endpoint."""
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query", json=payload, timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get("rows", []) or body.get("results", [])
    return []


def ws_execute(sql: str) -> dict:
    """Execute DDL/DML via write_service /execute endpoint."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/execute", json={"sql": sql}, timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def read_attestation_engine_source() -> str:
    """Read attestation_engine.py source code."""
    try:
        return ATTESTATION_ENGINE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def check_source_for_dynamic_evidence(source: str) -> dict[str, Any]:
    """Check attestation_engine.py source for dynamic evidence references."""
    results = {
        "source_found": bool(source),
        "references_pi_scorer": False,
        "references_injection_resilience": False,
        "references_enricher": False,
        "patterns_found": {},
    }

    if not source:
        return results

    source_lower = source.lower()

    # Check for pi_scorer references
    for pattern in DYNAMIC_EVIDENCE_PATTERNS["pi_scorer_output"]:
        if re.search(pattern, source_lower):
            results["references_pi_scorer"] = True
            results["patterns_found"].setdefault("pi_scorer", []).append(pattern)

    # Check for injection_resilience references
    for pattern in DYNAMIC_EVIDENCE_PATTERNS["injection_resilience"]:
        if re.search(pattern, source_lower):
            results["references_injection_resilience"] = True
            results["patterns_found"].setdefault("injection_resilience", []).append(pattern)

    # Check for enricher output references
    for pattern in DYNAMIC_EVIDENCE_PATTERNS["enricher_output"]:
        if re.search(pattern, source_lower):
            results["references_enricher"] = True
            results["patterns_found"].setdefault("enricher", []).append(pattern)

    return results


def check_attestation_text_for_dynamic_evidence(text: str) -> dict[str, Any]:
    """Check a single attestation text for dynamic evidence references."""
    findings = {
        "has_pi_scorer_ref": False,
        "has_injection_resilience_ref": False,
        "has_enricher_ref": False,
        "patterns_matched": [],
    }

    text_lower = text.lower()

    for pattern in DYNAMIC_EVIDENCE_PATTERNS["pi_scorer_output"]:
        if re.search(pattern, text_lower):
            findings["has_pi_scorer_ref"] = True
            findings["patterns_matched"].append(pattern)

    for pattern in DYNAMIC_EVIDENCE_PATTERNS["injection_resilience"]:
        if re.search(pattern, text_lower):
            findings["has_injection_resilience_ref"] = True
            findings["patterns_matched"].append(pattern)

    for pattern in DYNAMIC_EVIDENCE_PATTERNS["enricher_output"]:
        if re.search(pattern, text_lower):
            findings["has_enricher_ref"] = True
            findings["patterns_matched"].append(pattern)

    return findings


def query_recent_attestations(limit: int = 100) -> list[dict]:
    """Query recent attestations from mcp_attestations table."""
    sql = """
    SELECT 
        server_id,
        attestation_text,
        scope,
        confidence_level,
        valid_until,
        generated_at
    FROM mcp_attestations
    ORDER BY generated_at DESC
    LIMIT ?
    """
    try:
        return ws_query(sql, [limit])
    except Exception:
        return []


def verify_dynamic_evidence() -> dict[str, Any]:
    """Main verification logic - check source and attestations for dynamic evidence."""
    results = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "source_analysis": {},
        "attestation_analysis": {
            "total_checked": 0,
            "with_dynamic_evidence": 0,
            "without_dynamic_evidence": 0,
            "with_injection_resilience": 0,
        },
        "verdict": "UNKNOWN",
        "recommendation": "",
        "extension_needed": False,
    }

    # Step 1: Check attestation_engine.py source
    source_code = read_attestation_engine_source()
    source_results = check_source_for_dynamic_evidence(source_code)
    results["source_analysis"] = source_results

    # Step 2: Query attestations and check for dynamic evidence
    attestations = query_recent_attestations()
    results["attestation_analysis"]["total_checked"] = len(attestations)

    for att in attestations:
        att_text = att.get("attestation_text", "") or ""
        evidence = check_attestation_text_for_dynamic_evidence(att_text)

        if evidence["has_injection_resilience_ref"]:
            results["attestation_analysis"]["with_injection_resilience"] += 1

        if (
            evidence["has_pi_scorer_ref"]
            or evidence["has_injection_resilience_ref"]
            or evidence["has_enricher_ref"]
        ):
            results["attestation_analysis"]["with_dynamic_evidence"] += 1
        else:
            results["attestation_analysis"]["without_dynamic_evidence"] += 1

    # Step 3: Determine verdict and recommendation
    has_source_integration = (
        source_results["references_pi_scorer"]
        or source_results["references_injection_resilience"]
        or source_results["references_enricher"]
    )

    attestation_dynamic_ratio = 0.0
    total = results["attestation_analysis"]["total_checked"]
    if total > 0:
        attestation_dynamic_ratio = (
            results["attestation_analysis"]["with_dynamic_evidence"] / total
        )

    # Phase 8 verdict logic
    if has_source_integration and attestation_dynamic_ratio >= 0.5:
        results["verdict"] = "PASS"
        results["recommendation"] = (
            "Attestation engine properly cites dynamic evidence. "
            "Phase 8 pi_scorer integration is working."
        )
        results["extension_needed"] = False
    elif has_source_integration and attestation_dynamic_ratio < 0.5:
        results["verdict"] = "PARTIAL"
        results["recommendation"] = (
            "Attestation engine has source integration but attestations "
            "are not citing dynamic evidence. Check enricher wiring."
        )
        results["extension_needed"] = False
    elif not has_source_integration and attestation_dynamic_ratio > 0:
        results["verdict"] = "EXTENSION_NEEDED"
        results["recommendation"] = (
            "Attestations reference dynamic evidence but attestation_engine.py "
            "does not. An extension module is needed per Phase 8 completion notes."
        )
        results["extension_needed"] = True
    else:
        results["verdict"] = "FAIL"
        results["recommendation"] = (
            "No dynamic evidence integration found. Extension is needed per "
            "Phase 8 completion notes to add injection_resilience references."
        )
        results["extension_needed"] = True

    return results


def print_report(results: dict[str, Any]) -> None:
    """Print verification report to stdout."""
    print("=" * 70)
    print("DYNAMIC EVIDENCE VERIFICATION REPORT")
    print("=" * 70)
    print(f"Verified at: {results['verified_at']}")
    print()

    # Source analysis
    print("--- SOURCE CODE ANALYSIS ---")
    source = results["source_analysis"]
    print(f"  attestation_engine.py found: {source.get('source_found', False)}")
    print(f"  References pi_scorer: {source.get('references_pi_scorer', False)}")
    print(f"  References injection_resilience: {source.get('references_injection_resilience', False)}")
    print(f"  References enricher: {source.get('references_enricher', False)}")
    if source.get("patterns_found"):
        print(f"  Patterns found: {source['patterns_found']}")
    print()

    # Attestation analysis
    print("--- ATTESTATION ANALYSIS ---")
    att = results["attestation_analysis"]
    print(f"  Total attestations checked: {att['total_checked']}")
    print(f"  With dynamic evidence: {att['with_dynamic_evidence']}")
    print(f"  Without dynamic evidence: {att['without_dynamic_evidence']}")
    print(f"  With injection_resilience ref: {att['with_injection_resilience']}")
    print()

    # Verdict
    print("--- VERDICT ---")
    verdict_color = (
        "\033[92m"  # green
        if results["verdict"] == "PASS"
        else "\033[93m"  # yellow
        if results["verdict"] == "PARTIAL"
        else "\033[91m"  # red
    )
    reset = "\033[0m"
    print(f"  {verdict_color}{results['verdict']}{reset}")
    print(f"  Recommendation: {results['recommendation']}")
    print(f"  Extension needed: {results['extension_needed']}")
    print("=" * 70)


if __name__ == "__main__":
    print("Starting dynamic evidence verification...")

    try:
        results = verify_dynamic_evidence()
        print_report(results)

        # Exit code based on verdict
        if results["verdict"] == "PASS":
            sys.exit(0)
        elif results["verdict"] == "PARTIAL":
            sys.exit(0)  # Partial is acceptable
        else:
            sys.exit(1)  # FAIL or EXTENSION_NEEDED

    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Could not connect to write_service at {WRITE_SERVICE_URL}: {e}")
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: Verification failed: {e}")
        sys.exit(2)