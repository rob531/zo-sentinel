#!/usr/bin/env python
# -*- coding: utf-8 -*-

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from io import StringIO
from typing import List, Dict

# Tier constants
TRUSTED_GENERAL = "TRUSTED_GENERAL"
TRUSTED_RESEARCH = "TRUSTED_RESEARCH"
ENTERPRISE_CONTROLLED = "ENTERPRISE_CONTROLLED"
CAUTION_LIMITED = "CAUTION_LIMITED"
HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
KNOWN_THREAT = "KNOWN_THREAT"

KNOWN_TIERS = {
    TRUSTED_GENERAL,
    TRUSTED_RESEARCH,
    ENTERPRISE_CONTROLLED,
    CAUTION_LIMITED,
    HIGH_RISK_ISOLATED,
    KNOWN_THREAT,
}


def classify_tier(p_top: float, escalated: bool = False, has_critical: bool = False) -> str:
    """
    Determine the risk tier for a server.

    Args:
        p_top: Probability of top risk.
        escalated: Whether the server has been escalated.
        has_critical: Whether a critical probability is present.

    Returns:
        One of the tier constants.
    """
    if escalated:
        return HIGH_RISK_ISOLATED
    if p_top >= 0.9:
        return HIGH_RISK_ISOLATED
    if p_top >= 0.5:
        return ENTERPRISE_CONTROLLED
    if p_top >= 0.2:
        return CAUTION_LIMITED
    return KNOWN_THREAT


def build_rollup(rows: List[Dict]) -> Dict:
    """
    Build a rollup report from raw axis score rows.

    Only rows where axis_name == "overall_risk" are considered for tiering.

    Returns a dict with the following structure:
    {
        "generated_at": ISO8601 timestamp,
        "total_servers": int,
        "tiers": {tier_name: count, ...},
        "escalated_servers": int,
        "unknown_tier_servers": int,
        "rows": [
            {"server_id": str, "tier": str, "p_top": float, "escalated": bool},
            ...
        ],
    }
    """
    overall_rows = [
        r for r in rows if r.get("axis_name") == "overall_risk"
    ]

    tier_counts = Counter()
    escalated_count = 0
    unknown_tier_count = 0
    result_rows = []

    for r in overall_rows:
        server_id = r.get("server_id")
        p_top = float(r.get("p_top", 0.0))
        escalated = bool(r.get("escalated", False))
        p_critical = float(r.get("p_critical", 0.0))
        has_critical = p_critical > 0.0

        tier = classify_tier(p_top, escalated=escalated, has_critical=has_critical)

        if tier not in KNOWN_TIERS:
            unknown_tier_count += 1
        else:
            tier_counts[tier] += 1

        if escalated:
            escalated_count += 1

        result_rows.append(
            {
                "server_id": server_id,
                "tier": tier,
                "p_top": p_top,
                "escalated": escalated,
            }
        )

    rollup = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_servers": len(overall_rows),
        "tiers": dict(tier_counts),
        "escalated_servers": escalated_count,
        "unknown_tier_servers": unknown_tier_count,
        "rows": result_rows,
    }
    return rollup


def to_csv(rollup: Dict) -> str:
    """
    Convert a rollup dict to CSV format.

    Header: server_id,tier,p_top,escalated
    """
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["server_id", "tier", "p_top", "escalated"])
    for row in rollup.get("rows", []):
        writer.writerow(
            [
                row.get("server_id"),
                row.get("tier"),
                f"{row.get('p_top'):.6f}",
                str(row.get("escalated")).lower(),
            ]
        )
    return output.getvalue()


if __name__ == "__main__":
    # Self‑test as per acceptance criteria
    test_rows = [
        # Overall risk rows
        {
            "server_id": "srv-1",
            "axis_name": "overall_risk",
            "p_top": 0.95,
            "p_critical": 0.0,
            "p_danger": 0.0,
            "escalated": False,
            "scored_at": "2023-01-01T00:00:00Z",
        },
        {
            "server_id": "srv-2",
            "axis_name": "overall_risk",
            "p_top": 0.55,
            "p_critical": 0.0,
            "p_danger": 0.0,
            "escalated": False,
            "scored_at": "2023-01-01T00:00:00Z",
        },
        {
            "server_id": "srv-3",
            "axis_name": "overall_risk",
            "p_top": 0.10,
            "p_critical": 0.0,
            "p_danger": 0.0,
            "escalated": False,
            "scored_at": "2023-01-01T00:00:00Z",
        },
        # Escalated row
        {
            "server_id": "srv-4",
            "axis_name": "overall_risk",
            "p_top": 0.20,
            "p_critical": 0.0,
            "p_danger": 0.0,
            "escalated": True,
            "scored_at": "2023-01-01T00:00:00Z",
        },
        # Non‑overall rows (should be ignored)
        {
            "server_id": "srv-5",
            "axis_name": "cpu_usage",
            "p_top": 0.99,
            "p_critical": 0.0,
            "p_danger": 0.0,
            "escalated": False,
            "scored_at": "2023-01-01T00:00:00Z",
        },
        {
            "server_id": "srv-6",
            "axis_name": "memory_pressure",
            "p_top": 0.30,
            "p_critical": 0.0,
            "p_danger": 0.0,
            "escalated": False,
            "scored_at": "2023-01-01T00:00:00Z",
        },
    ]

    # (a) Tier classification checks
    tier_a = classify_tier(0.95)
    tier_b = classify_tier(0.55)
    tier_c = classify_tier(0.10)
    assert tier_a == HIGH_RISK_ISOLATED, f"Expected {HIGH_RISK_ISOLATED}, got {tier_a}"
    assert tier_b == ENTERPRISE_CONTROLLED, f"Expected {ENTERPRISE_CONTROLLED}, got {tier_b}"
    assert tier_c == KNOWN_THREAT, f"Expected {KNOWN_THREAT}, got {tier_c}"

    # (b) Escalated row still HIGH_RISK_ISOLATED
    tier_d = classify_tier(0.20, escalated=True)
    assert tier_d == HIGH_RISK_ISOLATED, f"Escalated row expected {HIGH_RISK_ISOLATED}, got {tier_d}"

    # (c) Build rollup with extra non‑overall rows
    rollup = build_rollup(test_rows)
    assert rollup["total_servers"] == 4, f"Expected total_servers 4, got {rollup['total_servers']}"
    # Only the first three non‑escalated overall rows should map to the expected tiers
    expected_tiers = {
        HIGH_RISK_ISOLATED: 2,  # srv-1 and srv-4 (escalated)
        ENTERPRISE_CONTROLLED: 1,
        KNOWN_THREAT: 1,
    }
    assert rollup["tiers"] == expected_tiers, f"Tiers mismatch: {rollup['tiers']}"

    # (d) CSV output line count
    csv_output = to_csv(rollup)
    line_count = csv_output.strip().count("\n") + 1  # include header line
    assert line_count == 5, f"CSV should have 5 lines (header + 4 rows), got {line_count}"

    print("PASS")