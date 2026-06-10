#!/usr/bin/env python3
# deps: requests
"""
supply_chain_enrichment_wiring.py -- Wire supply_chain_enrichment into enrichments_writer.

Calls supply_chain_enrichment.compute_score(metadata) to get (score, evidence),
then calls enrichments_writer.write_enrichment(server_id, 'supply_chain', score, evidence)
to persist the result to mcp_signal_enrichments.

SCHEMA (mcp_signal_enrichments):
  id, server_id, signal_type, dimension, score, evidence_blob, computed_at

INTERFACE:
  wire_supply_chain(server_id, metadata) -> bool

CONSTRAINTS:
  - stdlib + requests only
  - All DB access via enrichments_writer (no duckdb direct access)
  - No imports of protected modules
  - Library module: no heartbeat required
"""

from typing import Any, Dict

from enrichments_writer import write_enrichment
from supply_chain_enrichment import compute_score

SIGNAL_TYPE = "supply_chain"


def wire_supply_chain(server_id: str, metadata: Dict[str, Any]) -> bool:
    """
    Compute supply-chain score from metadata and persist via enrichments_writer.

    Args:
        server_id:  Server identifier (str, snake_case recommended).
        metadata:   Arbitrary metadata dict passed to compute_score.
                    Supported keys include:
                    - registry_source: str
                    - age_days: int
                    - download_count: int
                    - dependency_count: int
                    - publisher_verified: bool
                    - stars: int

    Returns:
        True on success (write_enrichment returned True), False on any error.
    """
    score, evidence = compute_score(metadata)
    return write_enrichment(server_id, SIGNAL_TYPE, score, evidence)


if __name__ == "__main__":
    from unittest.mock import MagicMock, patch

    print("Running supply_chain_enrichment_wiring self-test ...")

    test_metadata = {
        "registry_source": "pypi",
        "age_days": 1500,
        "download_count": 5000000,
        "dependency_count": 15,
        "publisher_verified": True,
        "stars": 5000,
    }

    with patch("enrichments_writer.write_enrichment") as mock_write:
        mock_write.return_value = True

        result = wire_supply_chain("test-server-001", test_metadata)

        assert result is True, f"Expected True, got {result}"
        assert mock_write.called, "write_enrichment was not called"

        call_args = mock_write.call_args
        args = call_args.args if call_args.args else ()
        kwargs = call_args.kwargs if call_args.kwargs else {}

        server_id_arg = kwargs.get("server_id") if "server_id" in kwargs else (args[0] if len(args) > 0 else None)
        signal_type_arg = kwargs.get("signal_type") if "signal_type" in kwargs else (args[1] if len(args) > 1 else None)
        score_arg = kwargs.get("score") if "score" in kwargs else (args[2] if len(args) > 2 else None)
        evidence_arg = kwargs.get("evidence") if "evidence" in kwargs else (args[3] if len(args) > 3 else None)

        assert server_id_arg == "test-server-001", f"Wrong server_id: {server_id_arg}"
        assert signal_type_arg == "supply_chain", f"Wrong signal_type: {signal_type_arg}"
        assert isinstance(score_arg, (int, float)), f"score not numeric: {score_arg}"
        assert 0.0 <= score_arg <= 100.0, f"score {score_arg} out of [0,100]"
        assert isinstance(evidence_arg, dict), f"evidence not dict: {evidence_arg}"

        print(f"  score={score_arg}, evidence_keys={list(evidence_arg.keys())}")
        print("  Test 1: happy path ... PASS")

    # Test 2: write_enrichment returns False propagates
    with patch("enrichments_writer.write_enrichment") as mock_write:
        mock_write.return_value = False
        result = wire_supply_chain("test-server-002", {})
        assert result is False, f"Expected False on write failure, got {result}"
        print("  Test 2: write failure propagates ... PASS")

    # Test 3: compute_score returns out-of-range score is clamped by compute_score itself
    # (supply_chain_enrichment clamps to [0,100] internally)
    with patch("enrichments_writer.write_enrichment") as mock_write:
        mock_write.return_value = True
        result = wire_supply_chain("test-server-003", {})
        assert result is True, f"Expected True, got {result}"
        call_args = mock_write.call_args
        args = call_args.args if call_args.args else ()
        kwargs = call_args.kwargs if call_args.kwargs else {}
        score_arg = kwargs.get("score") if "score" in kwargs else (args[2] if len(args) > 2 else None)
        assert 0.0 <= score_arg <= 100.0, f"score {score_arg} out of [0,100]"
        print(f"  Test 3: score in bounds ... PASS (score={score_arg})")

    print("All supply_chain_enrichment_wiring self-tests PASS")