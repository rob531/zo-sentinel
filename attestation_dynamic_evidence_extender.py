#!/usr/bin/env python3
"""
attestation_dynamic_evidence_extender.py
Phase 8 closure: Augments attestation text with dynamic signal evidence.
Integrates with existing attestation_engine.py patterns.
"""

import json
import logging
from typing import Optional, Dict, Any

# stdlib only + requests for HTTP calls
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _augment_with_dynamic_evidence(
    attestation_text: str,
    evidence_blob: Optional[Dict[str, Any]],
    verdict: Optional[Dict[str, Any]] = None
) -> str:
    """
    Private helper: Augments attestation text with per-signal evidence fields.
    
    Args:
        attestation_text: Existing attestation text template
        evidence_blob: JSON blob containing signal scores and evidence
        verdict: Optional trust_synthesiser verdict row
        
    Returns:
        Updated attestation text with dynamic evidence citations
    """
    if not evidence_blob and not verdict:
        logger.info("No evidence_blob or verdict provided, returning original text")
        return attestation_text
    
    # Start with existing text or template marker
    sections = []
    
    # Check if original text has a placeholder for dynamic evidence
    if "<!-- DYNAMIC_EVIDENCE -->" in attestation_text:
        sections.append("<!-- DYNAMIC_EVIDENCE -->")
    elif "[DYNAMIC_EVIDENCE]" in attestation_text:
        sections.append("[DYNAMIC_EVIDENCE]")
    else:
        # Append to existing text
        sections.append(attestation_text)
    
    # Build dynamic evidence section header
    sections.append("\n## Dynamic Signal Evidence\n")
    
    # Process evidence_blob signals
    if evidence_blob:
        # Handle signals array in evidence_blob
        signals = evidence_blob.get("signals", [])
        if signals:
            sections.append("### Signal Scores\n")
            for signal in signals:
                signal_name = signal.get("name", signal.get("signal_type", "unknown"))
                score = signal.get("score", signal.get("value", "N/A"))
                confidence = signal.get("confidence", signal.get("weight", "N/A"))
                timestamp = signal.get("timestamp", signal.get("collected_at", "N/A"))
                
                sections.append(f"- **{signal_name}**: score={score}, confidence={confidence}, collected={timestamp}\n")
                
                # Include any additional evidence fields
                evidence_fields = {k: v for k, v in signal.items() 
                                   if k not in ('name', 'signal_type', 'score', 'value', 'confidence', 'weight', 'timestamp', 'collected_at')}
                if evidence_fields:
                    sections.append(f"  - evidence: {json.dumps(evidence_fields, indent=2)}\n")
        
        # Handle metrics in evidence_blob
        metrics = evidence_blob.get("metrics", {})
        if metrics:
            sections.append("\n### Metrics\n")
            for metric_name, metric_value in metrics.items():
                sections.append(f"- **{metric_name}**: {metric_value}\n")
        
        # Handle raw evidence blob content
        raw_fields = {k: v for k, v in evidence_blob.items() 
                      if k not in ('signals', 'metrics') and v}
        if raw_fields:
            sections.append("\n### Raw Evidence\n")
            sections.append(f"```json\n{json.dumps(raw_fields, indent=2)}\n```\n")
    
    # Process trust_synthesiser verdict
    if verdict:
        sections.append("\n## Trust Synthesis Verdict\n")
        trust_score = verdict.get("trust_score", verdict.get("score", "N/A"))
        verdict_text = verdict.get("verdict", verdict.get("assessment", "N/A"))
        confidence = verdict.get("confidence", verdict.get("certainty", "N/A"))
        
        sections.append(f"- **Trust Score**: {trust_score}\n")
        sections.append(f"- **Verdict**: {verdict_text}\n")
        sections.append(f"- **Confidence**: {confidence}\n")
        
        # Include supporting factors
        factors = verdict.get("factors", verdict.get("supporting_evidence", []))
        if factors:
            sections.append("\n### Supporting Factors\n")
            if isinstance(factors, list):
                for factor in factors:
                    sections.append(f"- {factor}\n")
            elif isinstance(factors, dict):
                for key, value in factors.items():
                    sections.append(f"- **{key}**: {value}\n")
        
        # Include any additional verdict fields
        verdict_details = {k: v for k, v in verdict.items()
                           if k not in ('trust_score', 'score', 'verdict', 'assessment', 'confidence', 'certainty', 'factors', 'supporting_evidence')}
        if verdict_details:
            sections.append("\n### Verdict Details\n")
            sections.append(f"```json\n{json.dumps(verdict_details, indent=2)}\n```\n")
    
    # Combine all sections
    result = "".join(sections)
    
    # Clean up duplicate newlines
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    
    logger.info(f"Augmented attestation text with dynamic evidence (blob size: {len(str(evidence_blob)) if evidence_blob else 0} bytes)")
    return result


# Configuration constants
READ_SERVICE_URL = "http://read_service:8080"
WRITE_SERVICE_URL = "http://write_service:8080"
ATTESTATIONS_ENDPOINT = "/api/v1/attestations"
SIGNALS_ENDPOINT = "/api/v1/signals"
VERDICT_ENDPOINT = "/api/v1/trust-synthesiser/verdict"


def _fetch_attestation(attestation_id: str) -> Optional[Dict[str, Any]]:
    """Fetch attestation record by id from mcp_attestations table via read_service."""
    try:
        response = requests.get(
            f"{READ_SERVICE_URL}{ATTESTATIONS_ENDPOINT}/{attestation_id}",
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch attestation {attestation_id}: {e}")
        return None


def _fetch_signal_scores(server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch signal scores with evidence_blob from mcp_signal_scores table via read_service."""
    try:
        response = requests.get(
            f"{READ_SERVICE_URL}{SIGNALS_ENDPOINT}/{server_id}/scores",
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch signal scores for {server_id}: {e}")
        return None


def _fetch_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch trust_synthesiser verdict row via read_service."""
    try:
        response = requests.get(
            f"{READ_SERVICE_URL}{VERDICT_ENDPOINT}/{server_id}",
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not fetch verdict for {server_id}: {e}")
        return None


def _write_attestation_via_service(attestation_id: str, attestation_data: Dict[str, Any]) -> bool:
    """
    Write updated attestation row to mcp_attestations via write_service HTTP.
    Only updates attestation_text, preserves all other columns.
    """
    try:
        response = requests.put(
            f"{WRITE_SERVICE_URL}{ATTESTATIONS_ENDPOINT}/{attestation_id}",
            json=attestation_data,
            timeout=30,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        logger.info(f"Successfully wrote attestation {attestation_id} via write_service")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to write attestation {attestation_id}: {e}")
        return False


def update_attestation_text(server_id: str, attestation_id: str) -> str:
    """
    Main entry point: reads existing attestation, augments with dynamic signal evidence,
    writes back via write_service, and returns updated text.
    
    This function runs within existing attestation_engine.py patterns and adds
    a new private _augment_with_dynamic_evidence() helper.
    
    Args:
        server_id: Server identifier
        attestation_id: Attestation record identifier from mcp_attestations
        
    Returns:
        Updated attestation text with dynamic evidence citations
    """
    # Step 1: Fetch existing attestation from mcp_attestations table
    attestation = _fetch_attestation(attestation_id)
    if not attestation:
        raise ValueError(f"Attestation not found: {attestation_id}")
    
    # Verify server_id matches (data integrity check)
    if attestation.get("server_id") != server_id:
        raise ValueError(
            f"Server ID mismatch for attestation {attestation_id}: "
            f"expected '{server_id}', found '{attestation.get('server_id')}'"
        )
    
    # Step 2: Extract original attestation text
    original_text = attestation.get("attestation_text", "")
    logger.info(f"Retrieved attestation {attestation_id} with text length: {len(original_text)}")
    
    # Step 3: Fetch signal scores evidence_blob from mcp_signal_scores table
    signal_scores = _fetch_signal_scores(server_id)
    evidence_blob = None
    if signal_scores:
        evidence_blob = signal_scores.get("evidence_blob")
        logger.info(f"Retrieved signal scores for server {server_id}, evidence_blob present: {evidence_blob is not None}")
    
    # Step 4: Fetch trust_synthesiser verdict row
    verdict = _fetch_verdict(server_id)
    if verdict:
        logger.info(f"Retrieved verdict for server {server_id}")
    
    # Step 5: Augment with dynamic evidence using private helper
    updated_text = _augment_with_dynamic_evidence(
        original_text,
        evidence_blob,
        verdict
    )
    
    # Step 6: Prepare updated attestation data (patch in-place)
    # Preserve all existing columns, only update attestation_text
    updated_attestation = dict(attestation)
    updated_attestation["attestation_text"] = updated_text
    updated_attestation["evidence_augmented"] = True  # Metadata flag
    
    # Step 7: Write updated attestation back via write_service HTTP
    if not _write_attestation_via_service(attestation_id, updated_attestation):
        raise RuntimeError(f"Failed to write updated attestation {attestation_id} via write_service")
    
    logger.info(
        f"Successfully updated attestation {attestation_id} with dynamic evidence "
        f"for server {server_id}"
    )
    
    return updated_text


if __name__ == "__main__":
    import sys
    
    # Default test values - replace with actual known values from live DB
    DEFAULT_SERVER_ID = "srv-001"
    DEFAULT_ATTESTATION_ID = "att-001"
    
    # Parse command line arguments
    if len(sys.argv) >= 3:
        server_id = sys.argv[1]
        attestation_id = sys.argv[2]
    elif len(sys.argv) == 2:
        # Only server_id provided, use default attestation_id
        server_id = sys.argv[1]
        attestation_id = DEFAULT_ATTESTATION_ID
    else:
        server_id = DEFAULT_SERVER_ID
        attestation_id = DEFAULT_ATTESTATION_ID
    
    print(f"Updating attestation {attestation_id} for server {server_id}")
    print("-" * 60)
    
    try:
        updated_text = update_attestation_text(server_id, attestation_id)
        
        # Verify evidence fields from signal blob are present
        evidence_markers = [
            "## Dynamic Signal Evidence",
            "## Trust Synthesis Verdict",
            "### Signal Scores",
            "### Metrics",
        ]
        
        text_content = updated_text
        found_markers = [m for m in evidence_markers if m in text_content]
        
        # Check for actual evidence_blob content (signals, scores, etc.)
        has_signal_data = "signals" in text_content.lower() or "score=" in text_content.lower()
        has_verdict_data = "trust_score" in text_content.lower() or "verdict" in text_content.lower()
        has_metrics = "### Metrics" in text_content
        
        # Assert evidence fields from signal blob are present
        assert found_markers, f"No evidence section markers found in: {text_content[:500]}"
        assert has_signal_data or has_verdict_data, f"No signal/verdict data found in text"
        
        print("\n" + "=" * 60)
        print("ACCEPTANCE TEST RESULT: PASS")
        print("=" * 60)
        print(f"\n✓ Found evidence section markers: {found_markers}")
        print(f"✓ Contains signal data: {has_signal_data}")
        print(f"✓ Contains verdict data: {has_verdict_data}")
        print(f"✓ Contains metrics: {has_metrics}")
        print(f"\n✓ Updated attestation text length: {len(updated_text)} chars")
        
        print("\n--- Updated Attestation Text (first 2000 chars) ---")
        print(updated_text[:2000])
        if len(updated_text) > 2000:
            print(f"\n... [{len(updated_text) - 2000} more characters] ...")
        
    except AssertionError as e:
        print("\n" + "=" * 60)
        print("ACCEPTANCE TEST RESULT: FAIL")
        print("=" * 60)
        print(f"Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        print("\n" + "=" * 60)
        print("ACCEPTANCE TEST RESULT: FAIL")
        print("=" * 60)
        print(f"Error: {e}")
        sys.exit(1)