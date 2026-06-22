#!/usr/bin/env python3
"""
investigate_tool_count_improvement.py

Investigation utility to analyze mcp_signal_scores for signal_type='tool_count'
and propose algorithm improvements for richer scoring.

Signal quality diagnostic shows tool_count has only 2 distinct values (range 55-92).
A signal needs at least 5 distinct values to be useful. This script reads metadata
fields (tool_count, tool_names, schema_complexity_score) to propose richer scoring.

Pure utility - no DB writes.
"""

import json
from collections import Counter
from typing import Any


def analyze_tool_count_signal(signal_scores_data: list[dict]) -> dict[str, Any]:
    """
    Analyze tool_count signal quality and propose improvements.

    Args:
        signal_scores_data: List of mcp_signal_scores records

    Returns:
        Dictionary containing analysis results and improvement proposals
    """
    # Filter for tool_count signal type
    tool_count_records = [
        r for r in signal_scores_data if r.get("signal_type") == "tool_count"
    ]

    if not tool_count_records:
        return {"error": "No tool_count signal records found"}

    # Extract score values
    scores = [r.get("score", 0) for r in tool_count_records]
    unique_scores = set(scores)

    analysis = {
        "signal_type": "tool_count",
        "basic_statistics": {
            "total_records": len(tool_count_records),
            "unique_values": len(unique_scores),
            "min_score": min(scores),
            "max_score": max(scores),
            "mean_score": round(sum(scores) / len(scores), 2),
            "score_distribution": dict(Counter(scores)),
        },
        "quality_assessment": {
            "meets_minimum_distinct_values": len(unique_scores) >= 5,
            "current_distinct_count": len(unique_scores),
            "required_distinct_count": 5,
            "deficiency": max(0, 5 - len(unique_scores)),
            "issue": (
                f"Only {len(unique_scores)} distinct values found, "
                f"need at least 5 for useful signal"
            ),
        },
    }

    return analysis


def analyze_metadata_for_richer_scoring(metadata_data: list[dict]) -> dict[str, Any]:
    """
    Analyze metadata fields to propose richer scoring algorithms.

    Args:
        metadata_data: List of metadata records

    Returns:
        Dictionary containing metadata analysis and scoring proposals
    """
    if not metadata_data:
        return {"error": "No metadata records found"}

    # Extract relevant fields
    tool_counts = [
        r.get("tool_count", 0)
        for r in metadata_data
        if r.get("tool_count") is not None
    ]
    complexity_scores = [
        r.get("schema_complexity_score", 0)
        for r in metadata_data
        if r.get("schema_complexity_score") is not None
    ]
    tool_names_list = [
        r.get("tool_names", [])
        for r in metadata_data
        if r.get("tool_names")
    ]

    analysis = {
        "metadata_summary": {
            "total_records": len(metadata_data),
            "tool_count": {
                "available": len(tool_counts) > 0,
                "min": min(tool_counts) if tool_counts else None,
                "max": max(tool_counts) if tool_counts else None,
                "mean": (
                    round(sum(tool_counts) / len(tool_counts), 2)
                    if tool_counts
                    else None
                ),
                "unique_values": len(set(tool_counts)),
            },
            "schema_complexity_score": {
                "available": len(complexity_scores) > 0,
                "min": min(complexity_scores) if complexity_scores else None,
                "max": max(complexity_scores) if complexity_scores else None,
                "mean": (
                    round(sum(complexity_scores) / len(complexity_scores), 2)
                    if complexity_scores
                    else None
                ),
                "unique_values": len(set(complexity_scores)),
            },
            "tool_names": {
                "available": len(tool_names_list) > 0,
                "non_empty_count": len(tool_names_list),
            },
        }
    }

    return analysis


def propose_improved_scoring_algorithms() -> dict[str, Any]:
    """
    Propose improved scoring algorithms based on available metadata.

    Returns:
        Dictionary containing proposed algorithms
    """
    proposals = {
        "summary": (
            "Proposed algorithms to achieve 5+ distinct values for tool_count signal"
        ),
        "current_issue": "tool_count signal has only 2 distinct values (55-92 range)",
        "algorithms": [
            {
                "name": "normalized_tool_count",
                "description": (
                    "Use raw tool_count with min-max normalization to 0-100 scale"
                ),
                "formula": "normalized = ((tool_count - min_count) / (max_count - min_count)) * 100",
                "expected_distinct_values": (
                    "Based on actual tool_count distribution (likely 10-50+ unique values)"
                ),
            },
            {
                "name": "quintile_tool_count",
                "description": (
                    "Assign scores 20, 40, 60, 80, 100 based on quintile placement"
                ),
                "formula": "score = ((quintile_rank) * 20) + 20",
                "expected_distinct_values": 5,
            },
            {
                "name": "composite_tool_complexity",
                "description": (
                    "Combine tool_count and schema_complexity_score for richer signal"
                ),
                "formula": (
                    "score = (tool_count_normalized * 0.5) + (complexity_normalized * 0.5)"
                ),
                "expected_distinct_values": (
                    "Multiple (combinatorial explosion of unique combinations)"
                ),
            },
            {
                "name": "tool_diversity_score",
                "description": "Score based on number and diversity of tool names",
                "formula": "score = (unique_tool_categories * 20) + (min(tool_count, 10) * 5)",
                "expected_distinct_values": "10+ based on category combinations",
            },
            {
                "name": "tiered_complexity_with_count",
                "description": "Tiered scoring: tool_count tier (1-5) * complexity factor",
                "formula": "score = min(tool_count_tier * 20, 100) * (1 + complexity_normalized)",
                "expected_distinct_values": "15-20 unique combinations",
            },
        ],
    }

    return proposals


def generate_investigation_report(
    signal_scores_data: list[dict],
    metadata_data: list[dict],
) -> dict[str, Any]:
    """
    Generate comprehensive investigation report.

    Args:
        signal_scores_data: mcp_signal_scores records
        metadata_data: metadata records

    Returns:
        Complete investigation report
    """
    report: dict[str, Any] = {
        "title": "Tool Count Signal Quality Investigation",
        "purpose": (
            "Diagnose why tool_count signal has only 2 distinct values "
            "and propose improvements"
        ),
        "sections": {},
    }

    # Section 1: Current signal analysis
    report["sections"]["current_signal_analysis"] = analyze_tool_count_signal(
        signal_scores_data
    )

    # Section 2: Metadata analysis
    report["sections"]["metadata_analysis"] = analyze_metadata_for_richer_scoring(
        metadata_data
    )

    # Section 3: Proposed improvements
    report["sections"]["improvement_proposals"] = propose_improved_scoring_algorithms()

    # Section 4: Recommendations
    recommendations: list[dict[str, str]] = []

    # Check if metadata has sufficient diversity
    if metadata_data:
        tc_unique = len(
            set(
                m.get("tool_count", 0)
                for m in metadata_data
                if m.get("tool_count") is not None
            )
        )
        cs_unique = len(
            set(
                m.get("schema_complexity_score", 0)
                for m in metadata_data
                if m.get("schema_complexity_score") is not None
            )
        )

        if tc_unique >= 5:
            recommendations.append({
                "priority": "HIGH",
                "action": "Use normalized_tool_count algorithm",
                "reason": (
                    f"tool_count field has {tc_unique} unique values - "
                    f"sufficient for 5+ distinct signal values"
                ),
            })
        else:
            recommendations.append({
                "priority": "HIGH",
                "action": "Combine multiple metadata fields",
                "reason": (
                    f"tool_count only has {tc_unique} unique values, "
                    f"need to combine with schema_complexity_score ({cs_unique} values)"
                ),
            })

        if cs_unique >= 5:
            recommendations.append({
                "priority": "MEDIUM",
                "action": "Consider schema_complexity_score as primary signal",
                "reason": f"schema_complexity_score has {cs_unique} unique values",
            })

    recommendations.append({
        "priority": "HIGH",
        "action": "Replace current tool_count algorithm",
        "reason": (
            "Current algorithm produces only 2 values, "
            "not useful for ranking/differentiation"
        ),
    })

    report["sections"]["recommendations"] = recommendations

    # Section 5: Implementation priority
    report["sections"]["implementation_priority"] = {
        "immediate": "normalized_tool_count or quintile_tool_count",
        "short_term": "composite_tool_complexity for richer signal",
        "long_term": "tool_diversity_score leveraging tool_names field",
    }

    return report


def print_report(report: dict[str, Any]) -> None:
    """Pretty print the investigation report."""
    print("=" * 80)
    print(f"  {report['title']}")
    print("=" * 80)
    print()

    # Current Signal Analysis
    print("1. CURRENT SIGNAL ANALYSIS")
    print("-" * 40)
    signal = report["sections"]["current_signal_analysis"]
    if "error" not in signal:
        stats = signal["basic_statistics"]
        quality = signal["quality_assessment"]
        print(f"   Total records: {stats['total_records']}")
        print(f"   Unique values: {stats['unique_values']}")
        print(f"   Score range: {stats['min_score']} - {stats['max_score']}")
        print(f"   Mean score: {stats['mean_score']}")
        print()
        print(f"   QUALITY ISSUE: {quality['issue']}")
        print(f"   Deficiency: Need {quality['deficiency']} more distinct values")
    else:
        print(f"   Error: {signal['error']}")
    print()

    # Metadata Analysis
    print("2. METADATA ANALYSIS (Available for richer scoring)")
    print("-" * 40)
    meta = report["sections"]["metadata_analysis"]
    if "error" not in meta:
        summary = meta["metadata_summary"]
        print(f"   Total metadata records: {summary['total_records']}")

        tc = summary["tool_count"]
        print("\n   tool_count field:")
        print(f"      Available: {'Yes' if tc['available'] else 'No'}")
        if tc["available"]:
            print(f"      Range: {tc['min']} - {tc['max']}")
            print(f"      Unique values: {tc['unique_values']}")

        cs = summary["schema_complexity_score"]
        print("\n   schema_complexity_score field:")
        print(f"      Available: {'Yes' if cs['available'] else 'No'}")
        if cs["available"]:
            print(f"      Range: {cs['min']} - {cs['max']}")
            print(f"      Unique values: {cs['unique_values']}")

        tn = summary["tool_names"]
        print("\n   tool_names field:")
        print(f"      Available: {'Yes' if tn['available'] else 'No'}")
        print(f"      Non-empty records: {tn['non_empty_count']}")
    print()

    # Improvement Proposals
    print("3. PROPOSED IMPROVED SCORING ALGORITHMS")
    print("-" * 40)
    proposals = report["sections"]["improvement_proposals"]
    for i, algo in enumerate(proposals["algorithms"], 1):
        print(f"\n   Algorithm {i}: {algo['name']}")
        print(f"   Description: {algo['description']}")
        print(f"   Formula: {algo['formula']}")
        print(f"   Expected distinct values: {algo['expected_distinct_values']}")
    print()

    # Recommendations
    print("4. RECOMMENDATIONS")
    print("-" * 40)
    for rec in report["sections"]["recommendations"]:
        priority_marker = "[HIGH]" if rec["priority"] == "HIGH" else "[MED]"
        print(f"   {priority_marker} {rec['action']}")
        print(f"      Reason: {rec['reason']}")
    print()

    # Implementation Priority
    print("5. IMPLEMENTATION PRIORITY")
    print("-" * 40)
    priority = report["sections"]["implementation_priority"]
    print(f"   Immediate: {priority['immediate']}")
    print(f"   Short-term: {priority['short_term']}")
    print(f"   Long-term: {priority['long_term']}")
    print()
    print("=" * 80)


def create_example_data() -> tuple[list[dict], list[dict]]:
    """Create example data to demonstrate the utility."""
    # Example signal_scores_data (showing current 2-distinct-value problem)
    signal_scores_data = [
        {"id": 1, "entity_id": "server_1", "signal_type": "tool_count", "score": 55},
        {"id": 2, "entity_id": "server_2", "signal_type": "tool_count", "score": 92},
        {"id": 3, "entity_id": "server_3", "signal_type": "tool_count", "score": 55},
        {"id": 4, "entity_id": "server_4", "signal_type": "tool_count", "score": 92},
        {"id": 5, "entity_id": "server_5", "signal_type": "tool_count", "score": 55},
        {"id": 6, "entity_id": "server_1", "signal_type": "response_time", "score": 85},
        {"id": 7, "entity_id": "server_2", "signal_type": "response_time", "score": 72},
    ]

    # Example metadata_data (showing rich data available)
    metadata_data = [
        {
            "entity_id": "server_1",
            "tool_count": 12,
            "tool_names": ["get_weather", "get_time", "calculate", "search"],
            "schema_complexity_score": 25,
        },
        {
            "entity_id": "server_2",
            "tool_count": 45,
            "tool_names": [
                "file_read", "file_write", "file_delete", "file_copy", "file_move",
                "dir_create", "dir_list", "dir_delete", "process_run", "process_kill",
            ],
            "schema_complexity_score": 78,
        },
        {
            "entity_id": "server_3",
            "tool_count": 8,
            "tool_names": ["query_db", "insert_record"],
            "schema_complexity_score": 15,
        },
        {
            "entity_id": "server_4",
            "tool_count": 67,
            "tool_names": [
                "api_call", "fetch_data", "parse_json", "validate_schema",
                "transform_data", "cache_set", "cache_get", "cache_clear",
                "log_event", "send_notification", "retry_request", "timeout_handler",
            ],
            "schema_complexity_score": 92,
        },
        {
            "entity_id": "server_5",
            "tool_count": 23,
            "tool_names": [
                "user_auth", "user_create", "user_update", "user_delete", "user_list",
            ],
            "schema_complexity_score": 45,
        },
    ]

    return signal_scores_data, metadata_data


if __name__ == "__main__":
    print("Running Tool Count Signal Investigation Utility")
    print()

    # Get example data
    signal_data, metadata_data = create_example_data()

    # Generate and print report
    report = generate_investigation_report(signal_data, metadata_data)
    print_report(report)

    # Also output as JSON for programmatic use
    print("\n[JSON Output for programmatic use]\n")
    print(json.dumps(report, indent=2))
