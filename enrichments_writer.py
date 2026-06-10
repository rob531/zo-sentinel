#!/usr/bin/env python3
# deps: requests
"""
enrichments_writer.py -- Pure enrichment pipeline writer.

Consumes output from enrichment modules (supply_chain_enrichment,
community_signal_enrichment, etc.) and writes rows to mcp_signal_enrichments
via write_service on :8772.

SCHEMA (mcp_signal_enrichments):
  id, server_id, signal_type, dimension, score, evidence_blob

INTERFACE:
  write_enrichment(server_id, signal_type, score, evidence) -> bool

CONSTRAINTS:
  - stdlib + requests only
  - All DB access via write_service HTTP (no duckdb direct access)
  - User-supplied values go through params (none here; rows dict is internal)
  - Library module: no heartbeat required
  - No imports of protected modules
"""

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
WRITE_TIMEOUT_SECS = 30


def write_enrichment(
    server_id: str,
    signal_type: str,
    score: float,
    evidence: Dict[str, Any] | None = None,
) -> bool:
    """
    Write one enrichment row to mcp_signal_enrichments via write_service.

    Args:
        server_id:    Server identifier (str, snake_case recommended).
        signal_type:  Signal kind (str, snake_case, e.g. 'supply_chain').
        score:        Risk / trust score (float, clamped to [0.0, 100.0]).
        evidence:     Arbitrary metadata dict (JSON-serialized into evidence_blob).
                      Defaults to {} if None.

    Returns:
        True on success (write_service returned ok:true), False on any error.

    Raises:
        No exceptions -- all errors are swallowed and return False.
    """
    # Input validation
    if not isinstance(server_id, str) or not server_id:
        return False
    if not isinstance(signal_type, str) or not signal_type:
        return False
    if not isinstance(score, (int, float)):
        return False
    if not (0.0 <= score <= 100.0):
        return False
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, dict):
        return False

    # Serialize evidence to JSON
    try:
        evidence_blob = json.dumps(evidence, separators=(",", ":"), sort_keys=True)
    except Exception:
        return False

    # Timestamp (ISO 8601)
    computed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Extract dimension from evidence if present, else derive from signal_type
    dimension = evidence.get("dimension") if isinstance(evidence, dict) else None
    if not dimension:
        dimension = signal_type

    row = {
        "server_id": server_id,
        "signal_type": signal_type,
        "dimension": dimension,
        "score": float(score),
        "evidence_blob": evidence_blob,
        "computed_at": computed_at,
    }

    payload = {
        "table": "mcp_signal_enrichments",
        "rows": row,
        "wait": True,
    }

    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json=payload,
            timeout=WRITE_TIMEOUT_SECS,
        )
        resp.raise_for_status()
        result = resp.json()
        return bool(result.get("ok", False))
    except Exception:
        return False


if __name__ == "__main__":
    # Self-test using unittest.mock to patch requests.post
    from unittest.mock import MagicMock, patch

    print("Running enrichments_writer self-test ...")

    # Test 1: Happy path -- correct table and keys
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        result = write_enrichment(
            "test-server", "supply_chain", 75.0, {"verdict": "medium_risk"}
        )
        assert result is True, f"Expected True, got {result}"
        assert mock_post.called, "requests.post was not called"

        call_args = mock_post.call_args
        assert call_args is not None, "call_args is None"

        # Extract URL from args or kwargs
        url = call_args.kwargs.get("url") if call_args.kwargs else None
        if url is None and call_args.args:
            url = call_args.args[0]
        assert url == "http://127.0.0.1:8772/write", f"Wrong URL: {url}"

        # Extract JSON payload
        payload = call_args.kwargs.get("json") if call_args.kwargs else None
        if payload is None and len(call_args) > 1:
            payload = call_args[1].get("json")
        assert payload is not None, "No JSON payload sent"
        assert (
            payload["table"] == "mcp_signal_enrichments"
        ), f"Wrong table: {payload.get('table')}"
        assert "rows" in payload, f"Missing 'rows' key: {list(payload.keys())}"
        rows = payload["rows"]
        assert rows["server_id"] == "test-server", f"Wrong server_id: {rows}"
        assert rows["signal_type"] == "supply_chain", f"Wrong signal_type: {rows}"
        assert rows["score"] == 75.0, f"Wrong score: {rows}"
        assert "evidence_blob" in rows, f"Missing evidence_blob: {rows}"
        assert rows["evidence_blob"] == '{"verdict":"medium_risk"}', (
            f"Wrong evidence_blob: {rows.get('evidence_blob')}"
        )
        print("  Test 1: happy path ... PASS")

    # Test 2: HTTP error returns False
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_post.return_value = mock_response

        result = write_enrichment(
            "test-server", "supply_chain", 75.0, {"verdict": "medium_risk"}
        )
        assert result is False, f"Expected False on HTTP error, got {result}"
        print("  Test 2: HTTP error returns False ... PASS")

    # Test 3: Out-of-range score is rejected without calling write_service
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        result = write_enrichment("test-server", "supply_chain", 150.0, {})
        assert result is False, f"Expected False for out-of-range score, got {result}"
        assert not mock_post.called, (
            "requests.post should not be called for out-of-range score"
        )
        print("  Test 3: out-of-range score rejected ... PASS")

    # Test 4: None evidence defaults to {}
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        result = write_enrichment("test-server", "supply_chain", 75.0, None)
        assert result is True, f"Expected True with None evidence, got {result}"
        assert mock_post.called, "requests.post was not called for None evidence"
        call_args = mock_post.call_args
        payload = call_args.kwargs.get("json") if call_args.kwargs else None
        if payload is None and len(call_args) > 1:
            payload = call_args[1].get("json")
        assert payload is not None
        assert payload["rows"]["evidence_blob"] == "{}", (
            f"None evidence should serialize to '{{}}', got {payload['rows']['evidence_blob']}"
        )
        print("  Test 4: None evidence defaults to {} ... PASS")

    print("All enrichments_writer self-tests PASS")
    sys.exit(0)
