"""
investigate_known_bad_pattern_improvement.py

Investigates the known_bad_pattern signal scoring and proposes improvements.
Signal quality diagnostic shows known_bad_pattern has only 2 distinct values
(range 69-95). This utility analyzes actual values and proposes algorithm
improvements to increase discrimination.

Pure utility - no DB writes.
"""

import json
from collections import defaultdict
from typing import Any


def analyze_known_bad_pattern_values(
    mcp_signal_scores: list[dict],
    metadata_fields: list[str] | None = None
) -> dict[str, Any]:
    """
    Analyze known_bad_pattern signal values and propose improvements.

    Args:
        mcp_signal_scores: List of signal score records
        metadata_fields: Fields to analyze for partial score building

    Returns:
        Analysis results and proposed algorithm
    """

    if metadata_fields is None:
        metadata_fields = [
            "pattern_type", "hit_count", "severity", "confidence",
            "category", "recency_score", "occurrence_count", "threat_level"
        ]

    # Filter for known_bad_pattern records
    known_bad_records = [
        r for r in mcp_signal_scores
        if r.get("signal_type") == "known_bad_pattern"
    ]

    # Extract scores
    scores = [r.get("score", 0) for r in known_bad_records]
    distinct_scores = set(scores)

    # Extract metadata values per record
    metadata_values: dict[str, dict[Any, list]] = defaultdict(lambda: defaultdict(list))

    for record in known_bad_records:
        metadata = record.get("metadata", {})
        score = record.get("score", 0)

        for field in metadata_fields:
            value = metadata.get(field)
            if value is not None:
                metadata_values[field][value].append(score)

    # Calculate field discriminative power
    field_analysis: dict[str, dict[str, Any]] = {}
    for field, value_scores in metadata_values.items():
        unique_score_groups = len(value_scores)  # How many distinct score groups this field creates
        score_variance = calculate_variance(list(value_scores.values()))

        field_analysis[field] = {
            "unique_values": len(value_scores),
            "score_groups": dict(value_scores),
            "discriminative_power": unique_score_groups / max(len(distinct_scores), 1),
            "variance": score_variance
        }

    return {
        "current_state": {
            "total_records": len(known_bad_records),
            "distinct_scores": len(distinct_scores),
            "score_range": (min(scores), max(scores)) if scores else (0, 0),
            "actual_distinct_values": sorted(distinct_scores)
        },
        "metadata_analysis": field_analysis,
        "proposed_algorithm": generate_improved_algorithm(field_analysis),
        "explanation": explain_weakness(field_analysis, distinct_scores)
    }


def calculate_variance(score_lists: list[list]) -> float:
    """Calculate variance of score means across groups."""
    if not score_lists:
        return 0.0
    means = [sum(g) / len(g) for g in score_lists if g]
    if len(means) < 2:
        return 0.0
    avg = sum(means) / len(means)
    return sum((m - avg) ** 2 for m in means) / len(means)


def explain_weakness(field_analysis: dict[str, dict[str, Any]], distinct_scores: set) -> str:
    """
    Explain why the current known_bad_pattern approach is weak.
    """
    explanation = """
    === WHY CURRENT known_bad_pattern APPROACH IS WEAK ===

    PROBLEM: Only 2 distinct score values (69-95) despite potentially
    diverse patterns in metadata.

    ROOT CAUSES:
    """

    # Identify unused discriminative fields
    unused_fields = [
        field for field, analysis in field_analysis.items()
        if analysis["unique_values"] > 1 and analysis["discriminative_power"] == 1.0
    ]

    if unused_fields:
        explanation += f"""
    1. UNUSED DISCRIMINATIVE FIELDS: Fields with multiple values are being
       ignored by the scoring algorithm:
       {unused_fields}

       These fields could provide nuanced scoring but aren't contributing.
    """

    # Check for fields with high potential
    high_potential = [
        (field, a) for field, a in field_analysis.items()
        if a["unique_values"] >= 3 and a["variance"] > 0
    ]

    if not high_potential:
        explanation += """
    2. HOMOGENEOUS SCORING: All records receive similar scores regardless
       of their metadata characteristics. The algorithm treats a low-threat
       pattern the same as a critical threat.
    """

    explanation += """
    CONSEQUENCES:
    - Cannot distinguish between minor and severe known bad patterns
    - Security team loses prioritization ability
    - False positives/negatives are harder to triage
    - No gradient of response is possible
    """

    return explanation


def generate_improved_algorithm(field_analysis: dict[str, dict[str, Any]]) -> str:
    """
    Generate improved scoring algorithm code snippet.
    """

    # Sort fields by discriminative power
    sorted_fields = sorted(
        field_analysis.items(),
        key=lambda x: (x[1]["discriminative_power"], x[1]["unique_values"]),
        reverse=True
    )

    algorithm_code = '''
# === IMPROVED known_bad_pattern SCORING ALGORITHM ===
# Proposed implementation for mcp_signal_scores

def calculate_known_bad_pattern_score(metadata: dict) -> float:
    """
    Calculate improved score using multiple metadata fields.

    Args:
        metadata: Record metadata with pattern information

    Returns:
        Discriminative score (0-100 scale)
    """
    # Base score for known bad pattern detection
    base_score = 60.0

    # Partial score components (cumulative adjustments)
    partial_scores = {}

    # 1. THREAT LEVEL CONTRIBUTION
    # Maps threat levels to score adjustments
    threat_mapping = {
        "critical": 20.0,
        "high": 15.0,
        "medium": 8.0,
        "low": 3.0,
        "info": 0.0
    }
    threat_level = metadata.get("threat_level", "medium")
    partial_scores["threat_level"] = threat_mapping.get(threat_level, 5.0)

    # 2. SEVERITY CONTRIBUTION
    # Numeric severity (0-10 scale) mapped to score range
    severity = metadata.get("severity", 5)
    severity_score = (severity / 10.0) * 10.0  # 0-10 points
    partial_scores["severity"] = severity_score

    # 3. RECENCY CONTRIBUTION
    # More recent patterns get higher scores
    recency = metadata.get("recency_score", 0.5)
    recency_score = recency * 5.0  # 0-5 points
    partial_scores["recency"] = recency_score

    # 4. OCCURRENCE FREQUENCY CONTRIBUTION
    # Common patterns vs rare patterns
    occurrences = metadata.get("occurrence_count", 1)
    if occurrences > 100:
        freq_score = 5.0
    elif occurrences > 20:
        freq_score = 3.0
    elif occurrences > 5:
        freq_score = 2.0
    else:
        freq_score = 1.0
    partial_scores["frequency"] = freq_score

    # 5. CATEGORY-BASED ADJUSTMENT
    # Certain categories warrant higher scores
    category_boost = {
        "malware": 8.0,
        "exploit": 10.0,
        "vulnerability": 6.0,
        "suspicious": 3.0,
        "benign": 0.0
    }
    category = metadata.get("category", "suspicious")
    partial_scores["category"] = category_boost.get(category, 2.0)

    # 6. PATTERN TYPE WEIGHTING
    # Different pattern types have different weights
    pattern_weights = {
        "signature": 3.0,
        "heuristic": 4.0,
        "behavioral": 5.0,
        "yara": 4.0
    }
    pattern_type = metadata.get("pattern_type", "signature")
    partial_scores["pattern_type"] = pattern_weights.get(pattern_type, 2.0)

    # Calculate total score
    total_adjustment = sum(partial_scores.values())
    final_score = min(100.0, base_score + total_adjustment)

    return round(final_score, 1)


# Example usage with score breakdown:
def score_with_breakdown(metadata: dict) -> dict:
    """Returns score with component breakdown."""
    base_score = 60.0
    partial_scores = {}

    # [Apply same logic as above, then...]

    return {
        "score": min(100.0, base_score + sum(partial_scores.values())),
        "base_score": base_score,
        "partial_scores": partial_scores,
        "total_adjustment": sum(partial_scores.values())
    }
'''

    # Add field importance summary
    importance_summary = "\n\n# FIELD IMPORTANCE RANKING:\n"
    for i, (field, analysis) in enumerate(sorted_fields[:6], 1):
        importance_summary += f"# {i}. {field}: {analysis['unique_values']} unique values, "
        importance_summary += f"power={analysis['discriminative_power']:.2f}\n"

    return algorithm_code + importance_summary


# Demo with synthetic data
def run_demo() -> None:
    """Run demonstration with synthetic data."""

    # Synthetic mcp_signal_scores with known_bad_pattern records
    demo_records = [
        {
            "signal_type": "known_bad_pattern",
            "score": 69,
            "metadata": {
                "threat_level": "low",
                "severity": 3,
                "recency_score": 0.2,
                "occurrence_count": 2,
                "category": "benign",
                "pattern_type": "signature"
            }
        },
        {
            "signal_type": "known_bad_pattern",
            "score": 95,
            "metadata": {
                "threat_level": "critical",
                "severity": 9,
                "recency_score": 0.9,
                "occurrence_count": 150,
                "category": "malware",
                "pattern_type": "behavioral"
            }
        },
        {
            "signal_type": "known_bad_pattern",
            "score": 72,
            "metadata": {
                "threat_level": "medium",
                "severity": 5,
                "recency_score": 0.5,
                "occurrence_count": 10,
                "category": "suspicious",
                "pattern_type": "heuristic"
            }
        },
        {
            "signal_type": "known_bad_pattern",
            "score": 91,
            "metadata": {
                "threat_level": "high",
                "severity": 8,
                "recency_score": 0.8,
                "occurrence_count": 45,
                "category": "exploit",
                "pattern_type": "yara"
            }
        },
        {
            "signal_type": "other_signal",
            "score": 50,
            "metadata": {}
        }
    ]

    print("=" * 70)
    print("KNOWN_BAD_PATTERN INVESTIGATION UTILITY")
    print("=" * 70)

    results = analyze_known_bad_pattern_values(demo_records)

    print("\n>>> CURRENT STATE <<<")
    state = results["current_state"]
    print(f"  Total known_bad_pattern records: {state['total_records']}")
    print(f"  Distinct scores: {state['distinct_scores']}")
    print(f"  Score range: {state['score_range']}")
    print(f"  Actual values: {state['actual_distinct_values']}")

    print("\n>>> METADATA FIELD ANALYSIS <<<")
    for field, analysis in results["metadata_analysis"].items():
        print(f"\n  {field}:")
        print(f"    Unique values in metadata: {analysis['unique_values']}")
        print(f"    Discriminative power: {analysis['discriminative_power']:.2f}")
        print(f"    Score variance: {analysis['variance']:.2f}")

    print("\n" + results["explanation"])

    print("\n>>> PROPOSED IMPROVED ALGORITHM <<<")
    print(results["proposed_algorithm"])

    # Demonstrate improved scoring
    print("\n>>> IMPROVED SCORING EXAMPLES <<<")
    test_metadata = demo_records[0]["metadata"]
    improved = _calculate_known_bad_pattern_score(test_metadata)
    print(f"\n  Original score: {demo_records[0]['score']}")
    print(f"  Improved score: {improved}")
    print(f"  (demonstrates wider discrimination)")


def _calculate_known_bad_pattern_score(metadata: dict) -> float:
    """Helper for demo - calculate improved score."""
    base_score = 60.0

    threat_mapping = {
        "critical": 20.0, "high": 15.0, "medium": 8.0, "low": 3.0, "info": 0.0
    }
    threat_level = metadata.get("threat_level", "medium")
    partial_scores = {"threat_level": threat_mapping.get(threat_level, 5.0)}

    severity = metadata.get("severity", 5)
    partial_scores["severity"] = (severity / 10.0) * 10.0

    recency = metadata.get("recency_score", 0.5)
    partial_scores["recency"] = recency * 5.0

    occurrences = metadata.get("occurrence_count", 1)
    if occurrences > 100:
        partial_scores["frequency"] = 5.0
    elif occurrences > 20:
        partial_scores["frequency"] = 3.0
    elif occurrences > 5:
        partial_scores["frequency"] = 2.0
    else:
        partial_scores["frequency"] = 1.0

    category_boost = {
        "malware": 8.0, "exploit": 10.0, "vulnerability": 6.0,
        "suspicious": 3.0, "benign": 0.0
    }
    category = metadata.get("category", "suspicious")
    partial_scores["category"] = category_boost.get(category, 2.0)

    pattern_weights = {
        "signature": 3.0, "heuristic": 4.0, "behavioral": 5.0, "yara": 4.0
    }
    pattern_type = metadata.get("pattern_type", "signature")
    partial_scores["pattern_type"] = pattern_weights.get(pattern_type, 2.0)

    total_adjustment = sum(partial_scores.values())
    final_score = min(100.0, base_score + total_adjustment)

    return round(final_score, 1)


if __name__ == "__main__":
    run_demo()
