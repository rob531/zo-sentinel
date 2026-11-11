from __future__ import annotations

from math import isclose
from typing import Callable


AXIS_NAMES = frozenset(
    {
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    }
)

_BANDS = (
    {"tier": "critical", "min": 80, "max": 100},
    {"tier": "high", "min": 60, "max": 79},
    {"tier": "medium", "min": 30, "max": 59},
    {"tier": "low", "min": 0, "max": 29},
)

_TIER_ORDER = {band["tier"]: index for index, band in enumerate(_BANDS)}

_LABEL_TO_TIER = {
    "critical": "critical",
    "very_critical": "critical",
    "severe": "critical",
    "high": "high",
    "danger": "high",
    "dangerous": "high",
    "medium": "medium",
    "moderate": "medium",
    "elevated": "medium",
    "low": "low",
    "very_low": "low",
    "minimal": "low",
    "minor": "low",
    "safe": "low",
    "none": "low",
    "no_risk": "low",
}


def _normalize_tier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _LABEL_TO_TIER.get(normalized, normalized)
    return normalized if normalized in _TIER_ORDER else None


def _allocate_shares(counts: list[int], total: int) -> list[float]:
    if total <= 0:
        return [0.0 for _ in counts]

    units = [count * 10_000 // total for count in counts]
    remainder = 10_000 - sum(units)
    if remainder:
        ranked = sorted(
            range(len(counts)),
            key=lambda index: (
                -(counts[index] * 10_000 - units[index] * total),
                index,
            ),
        )
        for index in ranked[:remainder]:
            units[index] += 1

    return [unit / 10_000 for unit in units]


def compute_band_histogram(
    axis_rows: list[dict],
    tier_of: Callable[[str], str | None] | None = None,
) -> dict:
    servers: dict[str, dict] = {}

    for row in axis_rows:
        server_id = str(row.get("server_id", ""))
        axis_name = row.get("axis_name")
        if axis_name not in AXIS_NAMES:
            continue
        server = servers.setdefault(server_id, {})
        if axis_name == "overall_risk":
            server["overall_risk"] = row

    counts = {band["tier"]: 0 for band in _BANDS}

    for server_id, server in servers.items():
        if tier_of is not None:
            tier = _normalize_tier(tier_of(server_id))
        else:
            overall_row = server.get("overall_risk")
            if overall_row is None:
                tier = None
            else:
                tier = _normalize_tier(overall_row.get("label"))

        if tier is not None:
            counts[tier] += 1

    total = sum(counts.values())
    unscored = len(servers) - total
    ordered_bands = sorted(
        _BANDS,
        key=lambda band: (_TIER_ORDER.get(band["tier"], len(_TIER_ORDER)), band["min"]),
    )
    shares = _allocate_shares([counts[band["tier"]] for band in ordered_bands], total)

    bands = [
        {
            "tier": band["tier"],
            "min": band["min"],
            "max": band["max"],
            "count": counts[band["tier"]],
            "share": share,
        }
        for band, share in zip(ordered_bands, shares)
    ]

    return {
        "bands": bands,
        "total": total,
        "unscored": unscored,
        "tiers": [band["tier"] for band in ordered_bands],
    }


def summarize_band_histogram(hist: dict) -> dict:
    bands = list(hist.get("bands") or [])
    total = int(hist.get("total", 0) or 0)
    unscored = int(hist.get("unscored", 0) or 0)

    if not bands or total <= 0:
        return {
            "dominant_tier": "",
            "total": total,
            "coverage": 0.0,
            "concentration": 0.0,
        }

    dominant = max(
        bands,
        key=lambda band: (
            int(band.get("count", 0) or 0),
            -_TIER_ORDER.get(band.get("tier"), len(_TIER_ORDER)),
            -int(band.get("min", 0) or 0),
        ),
    )
    dominant_count = int(dominant.get("count", 0) or 0)
    population = total + unscored

    return {
        "dominant_tier": str(dominant.get("tier", "")),
        "total": total,
        "coverage": (total / population) if population else 0.0,
        "concentration": (dominant_count / total) if total else 0.0,
    }


if __name__ == "__main__":
    all_server_rows: list[dict] = []
    for server_id, label, label_index, p_top in (
        ("server-critical", "critical", 3, 0.95),
        ("server-low", "low", 0, 0.10),
    ):
        for axis_name in sorted(AXIS_NAMES):
            all_server_rows.append(
                {
                    "server_id": server_id,
                    "axis_name": axis_name,
                    "p_top": p_top if axis_name == "overall_risk" else 0.25,
                    "label": label if axis_name == "overall_risk" else "adequate",
                    "label_index": label_index if axis_name == "overall_risk" else 1,
                }
            )

    missing_overall_rows = [
        {
            "server_id": "server-missing-overall",
            "axis_name": "auth_strength",
            "p_top": 0.20,
            "label": "adequate",
            "label_index": 1,
        }
    ]
    empty_rows: list[dict] = []

    histogram = compute_band_histogram(all_server_rows)
    assert sum(band["count"] for band in histogram["bands"]) == histogram["total"]
    assert isclose(
        sum(band["share"] for band in histogram["bands"]),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )

    missing_histogram = compute_band_histogram(missing_overall_rows)
    assert missing_histogram["unscored"] == 1
    assert missing_histogram["total"] == 0

    empty_histogram = compute_band_histogram(empty_rows)
    assert empty_histogram["total"] == 0
    assert empty_histogram["unscored"] == 0

    summary = summarize_band_histogram(histogram)
    highest_count = max(band["count"] for band in histogram["bands"])
    expected_dominant = next(
        band["tier"]
        for band in histogram["bands"]
        if band["count"] == highest_count
    )
    assert summary["dominant_tier"] == expected_dominant

    empty_summary = summarize_band_histogram(empty_histogram)
    assert empty_summary["total"] == 0
    assert empty_summary["coverage"] == 0.0

    print("PASS")