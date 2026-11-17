"""Logic for registry verdict confidence bands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


VERDICT_VALUES = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)

DEFAULT_BAND_EDGES = [0.0, 0.25, 0.5, 0.75, 1.0]


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _band_label(lower: float, upper: float) -> str:
    return f"{lower:.2f}-{upper:.2f}"


def compute_confidence_bands(
    rows: list[dict],
    band_edges: list[float] = [0.0, 0.25, 0.5, 0.75, 1.0],
) -> dict:
    """Compute confidence-band counts and verdict distributions.

    Bands are lower-inclusive and upper-exclusive, except that the final band
    includes its upper edge. Rows without a confidence score are ignored.
    """
    edges = sorted(float(edge) for edge in band_edges)
    if len(edges) < 2:
        raise ValueError("band_edges must contain at least two values")

    bands: dict[str, dict[str, Any]] = {}
    for lower, upper in zip(edges, edges[1:]):
        bands[_band_label(lower, upper)] = {
            "count": 0,
            "mean_trust_score": None,
            "verdict_counts": {},
        }

    trust_values: dict[str, list[float]] = {label: [] for label in bands}

    for row in rows:
        confidence = _as_float(row.get("confidence"))
        if confidence is None:
            continue

        label = None
        for index, (lower, upper) in enumerate(zip(edges, edges[1:])):
            if confidence >= lower and (
                confidence < upper or (index == len(edges) - 2 and confidence == upper)
            ):
                label = _band_label(lower, upper)
                break

        if label is None:
            continue

        band = bands[label]
        band["count"] += 1

        trust_score = _as_float(row.get("trust_score"))
        if trust_score is not None:
            trust_values[label].append(trust_score)

        verdict = row.get("verdict")
        if verdict in VERDICT_VALUES:
            verdict_counts = band["verdict_counts"]
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    for label, values in trust_values.items():
        if values:
            bands[label]["mean_trust_score"] = sum(values) / len(values)

    return bands


def fetch_registry_rows(db: Session) -> list[dict]:
    """Read scored registry rows from the application database."""
    result = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.confidence,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
        ).where(McpServerRegistry.confidence.is_not(None))
    )
    return [dict(row) for row in result.mappings().all()]


def get_registry_rows(db: Session) -> list[dict]:
    """Compatibility alias for callers that fetch registry rows."""
    return fetch_registry_rows(db)


if __name__ == "__main__":
    sample_rows = [
        {
            "server_id": "one",
            "verdict": "TRUSTED_GENERAL",
            "confidence": 0.1,
            "trust_score": 0.8,
            "risk_tier": "low",
        },
        {
            "server_id": "two",
            "verdict": "TRUSTED_GENERAL",
            "confidence": 0.4,
            "trust_score": 0.7,
            "risk_tier": "medium",
        },
        {
            "server_id": "three",
            "verdict": "CAUTION_LIMITED",
            "confidence": 0.6,
            "trust_score": 0.6,
            "risk_tier": "medium",
        },
        {
            "server_id": "four",
            "verdict": "CAUTION_LIMITED",
            "confidence": 0.9,
            "trust_score": 0.4,
            "risk_tier": "high",
        },
    ]
    result = compute_confidence_bands(sample_rows)
    assert sum(band["count"] for band in result.values()) == 4
    assert result["0.50-0.75"]["count"] == 1
    print("PASS")