"""
context_efficiency_enrichment.py

Pure enrichment module exposing compute_score(metadata: dict) -> (float, dict).

Scores how efficiently an MCP server uses context -- servers that require
large context windows, many round-trips, or verbose tool schemas score lower.

Higher scores = lean, efficient MCP servers.

Inputs:
  - tool_count: int -- number of tools exposed by the MCP server
  - avg_tool_desc_length: int -- average characters in tool descriptions
  - schema_complexity: int -- number of top-level schema keys (0 if unknown)
  - requires_context_window: bool -- whether the server needs large context
  - round_trip_estimate: int -- estimated API round trips per typical operation (0 if unknown)

All fields are optional; a missing field contributes 0 to the score and is
appended to evidence['missing'].

Formula (pure, no DB, no network):
  Base: 60.0
  + (30 - min(tool_count, 30)) * 0.8    (fewer tools = simpler, +0 to +24; 0 if missing)
  + (20 - min(avg_tool_desc_length, 20)) * 0.5  (shorter descs = cleaner, +0 to +10; 0 if missing)
  + (10 - min(schema_complexity, 10)) * 0.8   (simpler schema = better, +0 to +8; 0 if missing)
  + (10 if not requires_context_window else 0)  (no large context = +10; 0 if missing)
  - min(round_trip_estimate * 2, 15)      (many round trips = -15 to 0; 0 if missing)
  Clamp final to [0.0, 100.0].
"""

from __future__ import annotations

from typing import Any


SIGNAL_NAME = "context_efficiency"
VERSION = "1.0.0"
REQUIRED_FIELDS = frozenset([
    "tool_count",
    "avg_tool_desc_length",
    "schema_complexity",
    "requires_context_window",
    "round_trip_estimate",
])


def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compute context efficiency signal score from server metadata.

    Args:
        metadata: Server metadata dict. Expected optional keys:
            - tool_count: int
            - avg_tool_desc_length: int
            - schema_complexity: int
            - requires_context_window: bool
            - round_trip_estimate: int

    Returns:
        (score, evidence) where score in [0.0, 100.0]
    """
    # Collect missing fields
    missing: list[str] = [f for f in REQUIRED_FIELDS if f not in metadata]

    # Extract raw values (only used if field is present)
    tool_count_raw = metadata.get("tool_count")
    avg_desc_raw = metadata.get("avg_tool_desc_length")
    schema_raw = metadata.get("schema_complexity")
    ctx_win_raw = metadata.get("requires_context_window")
    round_trip_raw = metadata.get("round_trip_estimate")

    # Safe cast helpers
    def to_int(val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def to_bool(val: Any) -> bool | None:
        if val is None:
            return None
        try:
            return bool(val)
        except (TypeError, ValueError):
            return None

    tool_count = to_int(tool_count_raw)
    avg_desc = to_int(avg_desc_raw)
    schema = to_int(schema_raw)
    ctx_win = to_bool(ctx_win_raw)
    round_trip = to_int(round_trip_raw)

    # Compute components -- ONLY if field is present
    # When field is missing: contributes 0 (not max bonus from defaulting to 0)
    tool_component = (30 - min(tool_count, 30)) * 0.8 if tool_count is not None else 0.0
    desc_component = (20 - min(avg_desc, 20)) * 0.5 if avg_desc is not None else 0.0
    schema_component = (10 - min(schema, 10)) * 0.8 if schema is not None else 0.0
    context_bonus = 10.0 if (ctx_win is not None and not ctx_win) else 0.0
    round_trip_penalty = min(round_trip * 2, 15) if round_trip is not None else 0.0

    raw_score = (
        60.0
        + tool_component
        + desc_component
        + schema_component
        + context_bonus
        - round_trip_penalty
    )

    score = max(0.0, min(100.0, raw_score))

    # Confidence: 0.8 if all fields present, 0.4 if >=3 missing
    num_missing = len(missing)
    if num_missing == 0:
        confidence = 0.8
    elif num_missing >= 3:
        confidence = 0.4
    else:
        # 1 or 2 missing: linear interpolation between 0.4 and 0.8
        confidence = 0.8 - (num_missing * 0.2)

    evidence_blob: dict[str, Any] = {
        "tool_count": tool_count if tool_count is not None else 0,
        "avg_tool_desc_length": avg_desc if avg_desc is not None else 0,
        "schema_complexity": schema if schema is not None else 0,
        "requires_context_window": ctx_win if ctx_win is not None else False,
        "round_trip_estimate": round_trip if round_trip is not None else 0,
        "components": {
            "tool_score": round(tool_component, 4),
            "desc_score": round(desc_component, 4),
            "schema_score": round(schema_component, 4),
            "context_bonus": round(context_bonus, 4),
            "round_trip_penalty": round(round_trip_penalty, 4),
        },
    }

    evidence: dict[str, Any] = {
        "signal_type": SIGNAL_NAME,
        "confidence": confidence,
        "evidence_blob": evidence_blob,
        "missing": missing,
    }

    return round(score, 4), evidence


def compute_batch_scores(
    batch: list[dict[str, Any]],
) -> list[tuple[float, dict[str, Any]]]:
    """
    Compute scores for a batch of server metadata entries.
    """
    return [compute_score(item) for item in batch]


if __name__ == "__main__":
    import sys

    errors: list[str] = []

    # Test 1: all-missing base case
    score1, ev1 = compute_score({})
    if not (abs(score1 - 60.0) < 0.001):
        errors.append(f"Test 1 FAILED: expected score 60.0, got {score1}")
    else:
        print(f"Test 1 PASS: compute_score({{}}) score={score1}, missing={ev1['missing']}")

    # Test 2: lean efficient server
    score2, ev2 = compute_score({
        "tool_count": 3,
        "avg_tool_desc_length": 50,
        "schema_complexity": 2,
        "requires_context_window": False,
        "round_trip_estimate": 1,
    })
    if not (score2 > 80.0 and ev2["confidence"] >= 0.8):
        errors.append(
            f"Test 2 FAILED: expected score>80 and confidence>=0.8, "
            f"got score={score2} confidence={ev2['confidence']}"
        )
    else:
        print(
            f"Test 2 PASS: score={score2} (>80), "
            f"confidence={ev2['confidence']} (>=0.8)"
        )

    # Test 3: bloated inefficient server
    score3, ev3 = compute_score({
        "tool_count": 50,
        "requires_context_window": True,
        "round_trip_estimate": 8,
    })
    if not (score3 < 50.0 and len(ev3["missing"]) == 2):
        errors.append(
            f"Test 3 FAILED: expected score<50 and 2 missing, "
            f"got score={score3} missing={ev3['missing']}"
        )
    else:
        print(
            f"Test 3 PASS: score={score3} (<50), "
            f"missing={ev3['missing']} (2 entries)"
        )

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    else:
        print("PASS", file=sys.stderr)
        sys.exit(0)
