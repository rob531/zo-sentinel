"""
verdict_reasoning_text_profile_report.py

Provides utilities to profile the narrative justification fields of the
MCP server registry.
"""

from __future__ import annotations

import statistics
from typing import List, Dict, Any

import requests  # required by the project constraints, though not used here

from app.db import get_session
from app.models import McpServerRegistry


def profile_reasoning(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze a list of registry rows.

    Parameters
    ----------
    rows : list[dict]
        Each dict may contain the keys:
        - server_id
        - verdict
        - verdict_reasoning
        - confidence
        - risk_tier

    Returns
    -------
    dict
        {
            "total": int,
            "with_reasoning": int,
            "without_reasoning": int,
            "reasoning_len": {
                "min": int,
                "max": int,
                "mean": float,
                "median": float
            },
            "by_verdict": {
                verdict_str: {"count": int, "avg_reasoning_len": float},
                ...
            },
            "low_confidence_under_50": int
        }
    """
    total = len(rows)
    with_reasoning = 0
    without_reasoning = 0
    reasoning_lengths: List[int] = []
    by_verdict: Dict[str, Dict[str, Any]] = {}
    low_confidence_under_50 = 0

    for row in rows:
        verdict = str(row.get("verdict", "")) if row.get("verdict") is not None else ""
        reasoning = row.get("verdict_reasoning")
        if reasoning is None:
            reasoning = ""
        else:
            reasoning = str(reasoning)

        length = len(reasoning)
        if reasoning.strip():
            with_reasoning += 1
            reasoning_lengths.append(length)
        else:
            without_reasoning += 1
            # empty reasoning contributes length 0 to per‑verdict average
            reasoning_lengths.append(0)

        # aggregate per verdict
        if verdict not in by_verdict:
            by_verdict[verdict] = {"count": 0, "total_len": 0}
        by_verdict[verdict]["count"] += 1
        by_verdict[verdict]["total_len"] += length

        # low confidence count
        confidence = row.get("confidence")
        if confidence is not None:
            try:
                conf_val = float(confidence)
                if conf_val < 50:
                    low_confidence_under_50 += 1
            except (TypeError, ValueError):
                pass

    # reasoning length statistics
    if reasoning_lengths:
        min_len = min(reasoning_lengths)
        max_len = max(reasoning_lengths)
        mean_len = sum(reasoning_lengths) / len(reasoning_lengths)
        median_len = statistics.median(reasoning_lengths)
    else:
        min_len = max_len = 0
        mean_len = median_len = 0.0

    # finalize by_verdict structure
    by_verdict_final: Dict[str, Dict[str, Any]] = {}
    for verdict, data in by_verdict.items():
        count = data["count"]
        total_len = data["total_len"]
        avg_len = total_len / count if count else 0.0
        by_verdict_final[verdict] = {"count": count, "avg_reasoning_len": avg_len}

    return {
        "total": total,
        "with_reasoning": with_reasoning,
        "without_reasoning": without_reasoning,
        "reasoning_len": {
            "min": min_len,
            "max": max_len,
            "mean": mean_len,
            "median": median_len,
        },
        "by_verdict": by_verdict_final,
        "low_confidence_under_50": low_confidence_under_50,
    }


def fetch_registry_rows(limit: int = 5000) -> List[Dict[str, Any]]:
    """
    Retrieve rows from the MCP server registry.

    Parameters
    ----------
    limit : int, optional
        Maximum number of rows to fetch (default 5000).

    Returns
    -------
    list[dict]
        List of dictionaries with keys:
        server_id, verdict, verdict_reasoning, confidence, risk_tier
    """
    with get_session() as session:
        query = (
            session.query(
                McpServerRegistry.server_id,
                McpServerRegistry.verdict,
                McpServerRegistry.verdict_reasoning,
                McpServerRegistry.confidence,
                McpServerRegistry.risk_tier,
            )
            .limit(limit)
        )
        results = query.all()

    rows: List[Dict[str, Any]] = []
    for (
        server_id,
        verdict,
        verdict_reasoning,
        confidence,
        risk_tier,
    ) in results:
        rows.append(
            {
                "server_id": server_id,
                "verdict": verdict,
                "verdict_reasoning": verdict_reasoning,
                "confidence": confidence,
                "risk_tier": risk_tier,
            }
        )
    return rows


if __name__ == "__main__":
    import sys

    # Test case (a): empty list
    empty = []
    res_empty = profile_reasoning(empty)
    assert res_empty["total"] == 0
    assert res_empty["with_reasoning"] == 0
    assert res_empty["without_reasoning"] == 0
    assert res_empty["reasoning_len"] == {
        "min": 0,
        "max": 0,
        "mean": 0.0,
        "median": 0.0,
    }
    assert res_empty["by_verdict"] == {}
    assert res_empty["low_confidence_under_50"] == 0

    # Test case (b): 3 rows, one with 40‑char reasoning, one with None, one empty
    row1 = {
        "server_id": "s1",
        "verdict": "malicious",
        "verdict_reasoning": "x" * 40,
        "confidence": 30,
        "risk_tier": "high",
    }
    row2 = {
        "server_id": "s2",
        "verdict": "benign",
        "verdict_reasoning": None,
        "confidence": None,
        "risk_tier": "low",
    }
    row3 = {
        "server_id": "s3",
        "verdict": "unknown",
        "verdict_reasoning": "",
        "confidence": 60,
        "risk_tier": "medium",
    }
    three_rows = [row1, row2, row3]
    res_three = profile_reasoning(three_rows)
    assert res_three["total"] == 3
    assert res_three["with_reasoning"] == 1
    assert res_three["without_reasoning"] == 2
    assert res_three["reasoning_len"]["min"] == 0
    assert res_three["reasoning_len"]["max"] == 40
    assert abs(res_three["reasoning_len"]["mean"] - (40 / 3)) < 1e-9
    assert abs(res_three["reasoning_len"]["median"] - 0) < 1e-9
    assert set(res_three["by_verdict"].keys()) == {"malicious", "benign", "unknown"}
    assert res_three["by_verdict"]["malicious"]["count"] == 1
    assert abs(res_three["by_verdict"]["malicious"]["avg_reasoning_len"] - 40.0) < 1e-9
    assert res_three["by_verdict"]["benign"]["avg_reasoning_len"] == 0.0
    assert res_three["by_verdict"]["unknown"]["avg_reasoning_len"] == 0.0
    assert res_three["low_confidence_under_50"] == 1

    # Test case (c): 5 rows spanning three verdicts
    rows_c = [
        {
            "server_id": "a1",
            "verdict": "malicious",
            "verdict_reasoning": "reason A",
            "confidence": 45,
            "risk_tier": "high",
        },
        {
            "server_id": "a2",
            "verdict": "malicious",
            "verdict_reasoning": "reason B longer",
            "confidence": 55,
            "risk_tier": "high",
        },
        {
            "server_id": "b1",
            "verdict": "benign",
            "verdict_reasoning": None,
            "confidence": 20,
            "risk_tier": "low",
        },
        {
            "server_id": "c1",
            "verdict": "suspicious",
            "verdict_reasoning": "short",
            "confidence": 70,
            "risk_tier": "medium",
        },
        {
            "server_id": "c2",
            "verdict": "suspicious",
            "verdict_reasoning": "",
            "confidence": 49,
            "risk_tier": "medium",
        },
    ]
    res_c = profile_reasoning(rows_c)
    assert res_c["total"] == 5
    assert res_c["with_reasoning"] == 3  # A, B longer, short
    assert res_c["without_reasoning"] == 2
    lengths = [len("reason A"), len("reason B longer"), 0, len("short"), 0]
    assert res_c["reasoning_len"]["min"] == 0
    assert res_c["reasoning_len"]["max"] == max(lengths)
    assert abs(res_c["reasoning_len"]["mean"] - (sum(lengths) / 5)) < 1e-9
    assert abs(res_c["reasoning_len"]["median"] - statistics.median(lengths)) < 1e-9
    assert set(res_c["by_verdict"].keys()) == {"malicious", "benign", "suspicious"}
    assert res_c["by_verdict"]["malicious"]["count"] == 2
    assert abs(
        res_c["by_verdict"]["malicious"]["avg_reasoning_len"]
        - ((len("reason A") + len("reason B longer")) / 2)
    ) < 1e-9
    assert res_c["by_verdict"]["benign"]["count"] == 1
    assert res_c["by_verdict"]["benign"]["avg_reasoning_len"] == 0.0
    assert res_c["by_verdict"]["suspicious"]["count"] == 2
    assert abs(
        res_c["by_verdict"]["suspicious"]["avg_reasoning_len"]
        - ((len("short") + 0) / 2)
    ) < 1e-9
    # low confidence < 50: rows a1 (45), b1 (20), c2 (49) => 3
    assert res_c["low_confidence_under_50"] == 3

    print("PASS")
    sys.exit(0)