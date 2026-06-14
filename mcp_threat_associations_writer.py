#!/usr/bin/env python3
"""
mcp_threat_associations_writer.py

Daemon that consumes threat indicators from threat_correlator.py and
threat_feed_aggregator.py via read_service, deduplicates against existing
mcp_threat_associations rows, and writes correlated threat associations via
write_service.

Uses write_service at :8772 for both querying existing rows and writing new rows.
Polls every 15 minutes.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

import requests

# Configuration
READ_SERVICE_URL = "http://localhost:8771"
WRITE_SERVICE_URL = "http://localhost:8772"
POLL_INTERVAL_SECONDS = 15 * 60  # 15 minutes

# Source weights for confidence calculation
SOURCE_WEIGHTS = {
    "SHODAN": 0.8,
    "CVE": 0.9,
    "known_threats": 1.0,
}


def _query_existing_associations() -> Set[Tuple[str, str, str]]:
    """
    Query write_service for existing mcp_threat_associations rows.
    Returns a set of dedupe keys: (mcp_identifier, threat_type, source).
    """
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"table": "mcp_threat_associations"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("rows", [])
        dedupe_keys = set()
        for row in rows:
            dedupe_keys.add((row["mcp_identifier"], row["threat_type"], row["source"]))
        return dedupe_keys
    except requests.exceptions.RequestException as e:
        print(f"Error querying existing associations: {e}")
        return set()


def _fetch_pending_indicators() -> List[Dict[str, Any]]:
    """
    Fetch pending threat indicators from threat_correlator.py and
    threat_feed_aggregator.py via read_service /query.
    """
    try:
        response = requests.post(
            f"{READ_SERVICE_URL}/query",
            json={"source": "threat_correlator"},
            timeout=30,
        )
        response.raise_for_status()
        correlator_indicators = response.json().get("rows", [])

        response = requests.post(
            f"{READ_SERVICE_URL}/query",
            json={"source": "threat_feed_aggregator"},
            timeout=30,
        )
        response.raise_for_status()
        aggregator_indicators = response.json().get("rows", [])

        return correlator_indicators + aggregator_indicators
    except requests.exceptions.RequestException as e:
        print(f"Error fetching pending indicators: {e}")
        return []


def _compute_confidence(source: str, indicator_quality: float) -> float:
    """
    Compute confidence as min(1.0, source_weight * indicator_quality).
    """
    source_weight = SOURCE_WEIGHTS.get(source, 0.5)
    return min(1.0, source_weight * indicator_quality)


def _dedupe_threats(
    indicators: List[Dict[str, Any]],
    existing_keys: Set[Tuple[str, str, str]],
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Deduplicate threat indicators against existing keys.
    Returns (new_associations, skipped_count).
    """
    new_associations = []
    skipped = 0

    for indicator in indicators:
        mcp_id = indicator["mcp_identifier"]
        threat_type = indicator["threat_type"]
        source = indicator["source"]
        dedupe_key = (mcp_id, threat_type, source)

        if dedupe_key in existing_keys:
            skipped += 1
            continue

        indicator_quality = indicator.get("indicator_quality", 1.0)
        confidence = _compute_confidence(source, indicator_quality)

        linked_signals = indicator.get("linked_signals", [])
        if isinstance(linked_signals, str):
            try:
                linked_signals = json.loads(linked_signals)
            except json.JSONDecodeError:
                linked_signals = [linked_signals]

        association = {
            "threat_assoc_id": str(uuid.uuid4()),
            "mcp_identifier": mcp_id,
            "threat_type": threat_type,
            "threat_indicator": indicator["threat_indicator"],
            "confidence": confidence,
            "source": source,
            "linked_signals": linked_signals,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        new_associations.append(association)
        existing_keys.add(dedupe_key)

    return new_associations, skipped


def _write_associations(associations: List[Dict[str, Any]]) -> bool:
    """
    Write threat associations to mcp_threat_associations table via write_service.
    """
    if not associations:
        return True

    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "mcp_threat_associations",
                "rows": associations,
                "wait": True,
            },
            timeout=60,
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error writing associations: {e}")
        return False


def run() -> None:
    """
    Main daemon loop. Polls every 15 minutes for new threat indicators,
    deduplicates, and writes to mcp_threat_associations.
    """
    print("Starting MCP Threat Associations Writer daemon...")

    while True:
        try:
            existing_keys = _query_existing_associations()
            print(f"Loaded {len(existing_keys)} existing dedupe keys")

            indicators = _fetch_pending_indicators()
            print(f"Fetched {len(indicators)} pending indicators")

            if indicators:
                new_associations, skipped = _dedupe_threats(indicators, existing_keys)
                print(f"Deduplication: {skipped} skipped, {len(new_associations)} new")

                if new_associations:
                    success = _write_associations(new_associations)
                    if success:
                        print(f"Wrote {len(new_associations)} threat associations")
                    else:
                        print("Failed to write associations")
        except Exception as e:
            print(f"Error in daemon loop: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


def _self_test() -> None:
    """
    Self-test: validates _dedupe_threats with 5 synthetic indicators (2 duplicates).
    Asserts exactly 3 rows written, confidence in [0,1], prints PASS.
    """
    # Simulate 3 existing rows (2 will be duplicates)
    existing_keys = {
        ("mcp-001", "malware", "SHODAN"),
        ("mcp-002", "phishing", "CVE"),
    }

    # 5 synthetic indicators (first 2 are duplicates of existing)
    test_indicators = [
        # Duplicate 1: exists in existing_keys
        {
            "mcp_identifier": "mcp-001",
            "threat_type": "malware",
            "threat_indicator": "bad-actor.example.com",
            "indicator_quality": 0.9,
            "source": "SHODAN",
            "linked_signals": ["sig-001"],
        },
        # Duplicate 2: exists in existing_keys
        {
            "mcp_identifier": "mcp-002",
            "threat_type": "phishing",
            "threat_indicator": "phish.example.com",
            "indicator_quality": 0.85,
            "source": "CVE",
            "linked_signals": ["sig-002"],
        },
        # New indicator 1
        {
            "mcp_identifier": "mcp-003",
            "threat_type": "c2",
            "threat_indicator": "192.0.2.1",
            "indicator_quality": 1.0,
            "source": "known_threats",
            "linked_signals": ["sig-003", "sig-004"],
        },
        # New indicator 2
        {
            "mcp_identifier": "mcp-004",
            "threat_type": "exploit",
            "threat_indicator": "CVE-2023-12345",
            "indicator_quality": 0.95,
            "source": "CVE",
            "linked_signals": ["sig-005"],
        },
        # New indicator 3
        {
            "mcp_identifier": "mcp-005",
            "threat_type": "recon",
            "threat_indicator": "scanner.detected.io",
            "indicator_quality": 0.8,
            "source": "SHODAN",
            "linked_signals": ["sig-006"],
        },
    ]

    new_associations, skipped = _dedupe_threats(test_indicators, existing_keys.copy())

    # Assertions per acceptance criteria
    assert len(new_associations) == 3, (
        f"Expected exactly 3 rows written, got {len(new_associations)}"
    )
    assert skipped == 2, f"Expected 2 duplicates skipped, got {skipped}"

    for assoc in new_associations:
        assert 0.0 <= assoc["confidence"] <= 1.0, (
            f"Confidence {assoc['confidence']} out of range [0,1]"
        )
        # Verify all required fields present
        assert "threat_assoc_id" in assoc
        assert "mcp_identifier" in assoc
        assert "threat_type" in assoc
        assert "threat_indicator" in assoc
        assert "confidence" in assoc
        assert "source" in assoc
        assert "linked_signals" in assoc
        assert "created_at" in assoc

    print("PASS: _dedupe_threats self-test passed")


if __name__ == "__main__":
    _self_test()
    run()