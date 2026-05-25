import logging
import hashlib
from typing import Any

from mcp_tool_schema_patterns import detect_tool_pattern

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("/home/workspace/logs/context_efficiency_enrichment.log")],
)
log = logging.getLogger("context_efficiency_enrichment")

SIGNAL_NAME = "context_efficiency"
VERSION = "1.0.0"
MAX_SCORE = 100.0
PROGRESSIVE_DISCLOSURE_TOOL_THRESHOLD = 4
BRUTE_FORCE_TOOL_THRESHOLD = 20


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + max(500.0, min(-500.0, -x)))


def softmax_weight(value: float, all_values: list[float]) -> float:
    if not all_values or max(all_values) == min(all_values):
        return 1.0 / max(1, len(all_values))
    exp_val = max(0.0, value)
    exp_sum = sum(max(0.0, v) for v in all_values)
    if exp_sum == 0:
        return 1.0 / max(1, len(all_values))
    return exp_val / exp_sum


def log_normalize(value: float) -> float:
    import math
    return math.log1p(max(0.0, value))


def hash_string(s: str) -> int:
    h = hashlib.sha256(s.encode("utf-8")).digest()
    return int.from_bytes(h[:4], byteorder="big")


def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compute context efficiency signal score from server metadata.

    Args:
        metadata: Server metadata dict. Expected keys:
            - tool_pattern: str ('progressive_disclosure' | 'brute_force' | 'hybrid')
            - tool_count: int (number of tools)
            - schema_complexity: float (normalized 0-1, e.g. avg param count / 10)
            - publisher_verified: bool

    Returns:
        (score, evidence) where score in [0.0, 100.0]
    """
    tool_pattern = metadata.get("tool_pattern", "unknown")
    tool_count = int(metadata.get("tool_count", 0))
    schema_complexity = float(metadata.get("schema_complexity", 0.0))
    publisher_verified = bool(metadata.get("publisher_verified", False))

    tool_patterns_all = ["progressive_disclosure", "brute_force", "hybrid", "unknown"]
    pattern_weights = {
        "progressive_disclosure": 1.0,
        "hybrid": 0.55,
        "brute_force": 0.0,
        "unknown": 0.3,
    }
    base_score = pattern_weights.get(tool_pattern, 0.3) * MAX_SCORE

    if tool_pattern == "progressive_disclosure":
        progressive_bonus = min(15.0, (PROGRESSIVE_DISCLOSURE_TOOL_THRESHOLD - tool_count) * 3.0)
        efficiency_score = base_score + progressive_bonus
    elif tool_pattern == "brute_force":
        excess = max(0, tool_count - BRUTE_FORCE_TOOL_THRESHOLD)
        brute_force_penalty = min(25.0, excess * 0.8)
        efficiency_score = base_score - brute_force_penalty
    elif tool_pattern == "hybrid":
        if tool_count <= PROGRESSIVE_DISCLOSURE_TOOL_THRESHOLD:
            hybrid_bonus = 10.0
        elif tool_count >= BRUTE_FORCE_TOOL_THRESHOLD:
            hybrid_penalty = 15.0
            hybrid_bonus = -hybrid_penalty
        else:
            mid_range = (BRUTE_FORCE_TOOL_THRESHOLD - PROGRESSIVE_DISCLOSURE_TOOL_THRESHOLD) / 2.0
            position = (tool_count - PROGRESSIVE_DISCLOSURE_TOOL_THRESHOLD) / mid_range
            hybrid_bonus = 10.0 * (1.0 - position) - 5.0 * position
        efficiency_score = base_score + hybrid_bonus
    else:
        efficiency_score = base_score

    complexity_contribution = schema_complexity * 5.0
    publisher_contribution = 4.0 if publisher_verified else 0.0

    efficiency_score = efficiency_score + complexity_contribution + publisher_contribution

    efficiency_score = max(0.0, min(100.0, efficiency_score))

    evidence = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "pattern_type": tool_pattern,
        "tool_count": tool_count,
        "schema_complexity": round(schema_complexity, 4),
        "publisher_verified": publisher_verified,
        "base_score": round(base_score, 4),
        "complexity_contribution": round(complexity_contribution, 4),
        "publisher_contribution": round(publisher_contribution, 4),
        "partial_scores": {
            "progressive_disclosure_bonus": (
                min(15.0, (PROGRESSIVE_DISCLOSURE_TOOL_THRESHOLD - tool_count) * 3.0)
                if tool_pattern == "progressive_disclosure"
                else 0.0
            ),
            "brute_force_penalty": (
                min(25.0, max(0, tool_count - BRUTE_FORCE_TOOL_THRESHOLD) * 0.8)
                if tool_pattern == "brute_force"
                else 0.0
            ),
        },
        "pattern_evidence": _build_pattern_evidence(metadata),
    }

    return round(efficiency_score, 4), evidence


def _build_pattern_evidence(metadata: dict[str, Any]) -> dict[str, Any]:
    tool_definitions = metadata.get("tool_definitions", [])
    if tool_definitions:
        pattern_result = detect_tool_pattern(tool_definitions)
        return pattern_result.get("evidence", {})
    return {
        "tools_analyzed": int(metadata.get("tool_count", 0)),
        "tools_with_schema": 0,
        "total_parameters": 0,
        "avg_description_length": 0.0,
        "has_dynamic_patterns": False,
        "reason": "tool_definitions not provided; evidence derived from metadata fields",
    }


def get_score_band(score: float) -> str:
    if score >= 80.0:
        return "EXCELLENT"
    elif score >= 60.0:
        return "GOOD"
    elif score >= 40.0:
        return "MODERATE"
    elif score >= 20.0:
        return "WEAK"
    else:
        return "POOR"


def compute_batch_scores(
    batch: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """
    Compute scores for a batch of server metadata.
    """
    results = []
    for item in batch:
        meta = item if isinstance(item, dict) else {"tool_pattern": "unknown", "tool_count": 0}
        score, evidence = compute_score(meta)
        results.append((score, evidence))
    return results


if __name__ == "__main__":
    test_metadata = {
        "tool_pattern": "progressive_disclosure",
        "tool_count": 3,
        "schema_complexity": 0.4,
        "publisher_verified": True,
    }
    score, evidence = compute_score(test_metadata)
    log.info("Progressive disclosure test score: %.4f", score)
    log.info("Evidence: %s", evidence)
    test2 = {"tool_pattern": "brute_force", "tool_count": 25, "schema_complexity": 0.8, "publisher_verified": False}
    score2, evidence2 = compute_score(test2)
    log.info("Brute force test score: %.4f", score2)
    log.info("Evidence: %s", evidence2)
    log.info("Signal: %s v%s | MAX_SCORE=%.1f", SIGNAL_NAME, VERSION, MAX_SCORE)
    log.info("Band for progressive: %s | Band for brute_force: %s", get_score_band(score), get_score_band(score2))
    sys.exit(0)

import sys