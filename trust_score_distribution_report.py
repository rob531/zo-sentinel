from __future__ import annotations

from statistics import median, quantiles
from typing import Any


BUCKET_DEFINITIONS = (
    ("0-20", 0.0, 20.0),
    ("20-40", 20.0, 40.0),
    ("40-60", 40.0, 60.0),
    ("60-80", 60.0, 80.0),
    ("80-100", 80.0, 100.0),
)


def _bucket_counts(scores: list[float]) -> list[dict[str, Any]]:
    counts = [0] * len(BUCKET_DEFINITIONS)

    for score in scores:
        bounded_score = min(max(score, 0.0), 100.0)
        for index, (_, minimum, maximum) in enumerate(BUCKET_DEFINITIONS):
            if minimum <= bounded_score < maximum or (
                index == len(BUCKET_DEFINITIONS) - 1 and bounded_score == maximum
            ):
                counts[index] += 1
                break

    return [
        {
            "label": label,
            "min": minimum,
            "max": maximum,
            "count": counts[index],
        }
        for index, (label, minimum, maximum) in enumerate(BUCKET_DEFINITIONS)
    ]


def _category_counts(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        category = value if value is not None else "unknown"
        counts[category] = counts.get(category, 0) + 1
    return {category: counts[category] for category in sorted(counts)}


def compute_trust_distribution(rows: list[dict]) -> dict:
    total = len(rows)
    scores: list[float] = []

    for row in rows:
        value = row.get("trust_score")
        if value is not None:
            scores.append(float(value))

    scored = len(scores)
    unscored = total - scored
    sorted_scores = sorted(scores)

    if scored:
        mean = sum(sorted_scores) / scored
        median_value = median(sorted_scores)
        if scored == 1:
            p25 = sorted_scores[0]
            p75 = sorted_scores[0]
        else:
            quartiles = quantiles(sorted_scores, n=4, method="inclusive")
            p25 = quartiles[0]
            p75 = quartiles[2]
    else:
        mean = None
        median_value = None
        p25 = None
        p75 = None

    return {
        "total": total,
        "scored": scored,
        "unscored": unscored,
        "buckets": _bucket_counts(sorted_scores),
        "by_tier": _category_counts(rows, "risk_tier"),
        "by_source": _category_counts(rows, "registry_source"),
        "mean": mean,
        "median": median_value,
        "p25": p25,
        "p75": p75,
    }


def _format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def render_markdown(summary: dict) -> str:
    total = int(summary.get("total", 0))
    scored = int(summary.get("scored", 0))
    unscored = int(summary.get("unscored", 0))
    buckets = summary.get("buckets", [])
    by_tier = summary.get("by_tier", {})
    by_source = summary.get("by_source", {})

    lines = [
        "# Trust Score Distribution",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total servers | {total} |",
        f"| Scored servers | {scored} |",
        f"| Unscored servers | {unscored} |",
        f"| Mean trust score | {_format_number(summary.get('mean'))} |",
        f"| Median trust score | {_format_number(summary.get('median'))} |",
        f"| 25th percentile | {_format_number(summary.get('p25'))} |",
        f"| 75th percentile | {_format_number(summary.get('p75'))} |",
        "",
        "## Trust Score Buckets",
        "",
        "| Bucket | Minimum | Maximum | Count |",
        "|---|---:|---:|---:|",
    ]

    if buckets:
        for bucket in buckets:
            lines.append(
                f"| {bucket.get('label', '')} | "
                f"{_format_number(bucket.get('min'))} | "
                f"{_format_number(bucket.get('max'))} | "
                f"{int(bucket.get('count', 0))} |"
            )
    else:
        lines.append("| No buckets | N/A | N/A | 0 |")

    lines.extend(
        [
            "",
            "## Servers by Risk Tier",
            "",
            "| Risk tier | Count |",
            "|---|---:|",
        ]
    )

    if by_tier:
        for tier, count in by_tier.items():
            lines.append(f"| {tier} | {int(count)} |")
    else:
        lines.append("| No risk tiers | 0 |")

    lines.extend(
        [
            "",
            "## Servers by Registry Source",
            "",
            "| Registry source | Count |",
            "|---|---:|",
        ]
    )

    if by_source:
        for source, count in by_source.items():
            lines.append(f"| {source} | {int(count)} |")
    else:
        lines.append("| No registry sources | 0 |")

    return "\n".join(lines) + "\n"


def run() -> dict:
    summary = compute_trust_distribution([])
    print(render_markdown(summary))
    return summary


if __name__ == "__main__":
    known_rows = [
        {
            "server_id": "server-1",
            "trust_score": 0.0,
            "risk_tier": "low",
            "registry_source": "official",
            "verdict": "approved",
        },
        {
            "server_id": "server-2",
            "trust_score": 25.0,
            "risk_tier": "medium",
            "registry_source": "community",
            "verdict": "approved",
        },
        {
            "server_id": "server-3",
            "trust_score": 75.0,
            "risk_tier": "high",
            "registry_source": "official",
            "verdict": "review",
        },
        {
            "server_id": "server-4",
            "trust_score": 100.0,
            "risk_tier": "low",
            "registry_source": "community",
            "verdict": "approved",
        },
        {
            "server_id": "server-5",
            "trust_score": None,
            "risk_tier": "unknown",
            "registry_source": "manual",
            "verdict": "pending",
        },
    ]

    summary = compute_trust_distribution(known_rows)
    assert summary["total"] == 5
    assert summary["scored"] == 4
    assert summary["unscored"] == 1
    assert summary["mean"] == 50.0
    assert [bucket["count"] for bucket in summary["buckets"]] == [1, 1, 0, 1, 1]

    markdown = render_markdown(summary)
    assert markdown
    assert "trust" in markdown.lower()
    for bucket in summary["buckets"]:
        assert bucket["label"] in markdown

    print("PASS")