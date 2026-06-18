"""
Tool Count Enrichment Module for MCP Package Scoring

Improves discrimination by combining tool_count with multiple metadata signals.
Current tool_count signal has only 2 distinct values across all MCPs (WEAK variety),
so this module creates a composite score using all available signals.
"""

from typing import Any


def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compute a composite quality score from multiple metadata fields.

    Combines tool_count (low variety) with other signals to improve
    discrimination between MCP packages.

    Args:
        metadata: Dictionary containing metadata fields:
            - tool_count: Number of tools in the package
            - registry_source: Source registry (official, community, etc.)
            - age_days: Package age in days
            - download_count: Total downloads
            - dependency_count: Number of dependencies
            - publisher_verified: Whether publisher is verified
            - stars: GitHub stars or equivalent

    Returns:
        Tuple of (score, evidence_dict) where:
            - score: Float in [0.0, 100.0]
            - evidence: Dict showing all fields used and their contributions
    """
    evidence: dict[str, Any] = {
        "fields_used": [],
        "field_scores": {},
        "weights": {},
        "calculation": {},
    }

    # Define weights for each signal (sum to 1.0)
    weights = {
        "tool_count": 0.25,
        "registry_source": 0.15,
        "age_days": 0.10,
        "download_count": 0.15,
        "dependency_count": 0.10,
        "publisher_verified": 0.10,
        "stars": 0.15,
    }
    evidence["weights"] = weights

    raw_values: dict[str, Any] = {}
    normalized_scores: dict[str, float] = {}

    # 1. tool_count (low variety, but still meaningful)
    tool_count = _safe_float(metadata.get("tool_count", 0))
    raw_values["tool_count"] = tool_count
    evidence["fields_used"].append("tool_count")

    if tool_count <= 0:
        tool_score = 0.0
    elif tool_count == 1:
        tool_score = 25.0
    elif tool_count == 2:
        tool_score = 50.0
    else:
        tool_score = min(100.0, 25.0 + (tool_count - 1) * 20.0)
    normalized_scores["tool_count"] = tool_score
    evidence["field_scores"]["tool_count"] = {
        "raw": tool_count,
        "normalized": tool_score,
        "contribution": tool_score * weights["tool_count"],
    }

    # 2. registry_source (trust/reputation signal)
    registry_source = str(metadata.get("registry_source", "unknown")).lower()
    raw_values["registry_source"] = registry_source
    evidence["fields_used"].append("registry_source")

    registry_scores = {
        "official": 100.0,
        "verified": 85.0,
        "curated": 75.0,
        "community": 50.0,
        "third_party": 40.0,
        "unknown": 20.0,
    }
    registry_score = registry_scores.get(registry_source, 30.0)
    normalized_scores["registry_source"] = registry_score
    evidence["field_scores"]["registry_source"] = {
        "raw": registry_source,
        "normalized": registry_score,
        "contribution": registry_score * weights["registry_source"],
    }

    # 3. age_days (maturity indicator)
    age_days = _safe_float(metadata.get("age_days", 0))
    raw_values["age_days"] = age_days
    evidence["fields_used"].append("age_days")

    if age_days <= 0:
        age_score = 15.0
    elif age_days < 30:
        age_score = 25.0
    elif age_days < 180:
        age_score = 40.0
    elif age_days < 365:
        age_score = 60.0
    elif age_days < 730:
        age_score = 75.0
    else:
        age_score = min(100.0, 80.0 + (age_days - 730) / 365 * 5)
    normalized_scores["age_days"] = age_score
    evidence["field_scores"]["age_days"] = {
        "raw": age_days,
        "normalized": age_score,
        "contribution": age_score * weights["age_days"],
    }

    # 4. download_count (popularity signal)
    download_count = _safe_float(metadata.get("download_count", 0))
    raw_values["download_count"] = download_count
    evidence["fields_used"].append("download_count")

    if download_count <= 0:
        download_score = 10.0
    elif download_count < 100:
        download_score = 20.0
    elif download_count < 1000:
        download_score = 40.0
    elif download_count < 10000:
        download_score = 60.0
    elif download_count < 100000:
        download_score = 80.0
    else:
        log_downloads = min(download_count, 10000000)
        download_score = min(100.0, 80.0 + (log_downloads / 100000) * 10)
    normalized_scores["download_count"] = download_score
    evidence["field_scores"]["download_count"] = {
        "raw": download_count,
        "normalized": download_score,
        "contribution": download_score * weights["download_count"],
    }

    # 5. dependency_count (integration/complexity signal)
    dependency_count = _safe_float(metadata.get("dependency_count", 0))
    raw_values["dependency_count"] = dependency_count
    evidence["fields_used"].append("dependency_count")

    if dependency_count <= 0:
        dep_score = 20.0
    elif dependency_count <= 3:
        dep_score = 60.0
    elif dependency_count <= 10:
        dep_score = 80.0
    elif dependency_count <= 25:
        dep_score = 70.0
    else:
        dep_score = max(40.0, 65.0 - (dependency_count - 25) * 1.0)
    normalized_scores["dependency_count"] = dep_score
    evidence["field_scores"]["dependency_count"] = {
        "raw": dependency_count,
        "normalized": dep_score,
        "contribution": dep_score * weights["dependency_count"],
    }

    # 6. publisher_verified (binary trust signal)
    publisher_verified = _safe_bool(metadata.get("publisher_verified", False))
    raw_values["publisher_verified"] = publisher_verified
    evidence["fields_used"].append("publisher_verified")

    verified_score = 100.0 if publisher_verified else 30.0
    normalized_scores["publisher_verified"] = verified_score
    evidence["field_scores"]["publisher_verified"] = {
        "raw": publisher_verified,
        "normalized": verified_score,
        "contribution": verified_score * weights["publisher_verified"],
    }

    # 7. stars (community approval signal)
    stars = _safe_float(metadata.get("stars", 0))
    raw_values["stars"] = stars
    evidence["fields_used"].append("stars")

    if stars <= 0:
        stars_score = 15.0
    elif stars < 10:
        stars_score = 30.0
    elif stars < 50:
        stars_score = 50.0
    elif stars < 200:
        stars_score = 70.0
    elif stars < 1000:
        stars_score = 85.0
    else:
        stars_score = min(100.0, 85.0 + (stars - 1000) / 9000 * 10)
    normalized_scores["stars"] = stars_score
    evidence["field_scores"]["stars"] = {
        "raw": stars,
        "normalized": stars_score,
        "contribution": stars_score * weights["stars"],
    }

    # Calculate final score (weighted sum, bounded)
    total_score = 0.0
    for field, score in normalized_scores.items():
        total_score += score * weights[field]

    final_score = max(0.0, min(100.0, round(total_score, 2)))

    evidence["raw_values"] = raw_values
    evidence["calculation"] = {
        "sum_of_weighted_scores": round(total_score, 2),
        "final_score": final_score,
    }
    evidence["discrimination_note"] = (
        "tool_count has low variety (2 values), but composite scoring with "
        f"{len(evidence['fields_used'])} signals improves discrimination."
    )

    return (final_score, evidence)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_bool(value: Any) -> bool:
    """Safely convert a value to boolean."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "verified")
    return bool(value)


if __name__ == "__main__":
    # Self-smoke: exercise the module against >=3 known-good inputs.
    test_cases = [
        {
            "label": "official, mature, popular",
            "meta": {
                "tool_count": 5,
                "registry_source": "official",
                "age_days": 800,
                "download_count": 50000,
                "dependency_count": 5,
                "publisher_verified": True,
                "stars": 500,
            },
        },
        {
            "label": "community, new, sparse",
            "meta": {
                "tool_count": 1,
                "registry_source": "community",
                "age_days": 10,
                "download_count": 50,
                "dependency_count": 1,
                "publisher_verified": False,
                "stars": 5,
            },
        },
        {
            "label": "unknown source, no metadata",
            "meta": {},
        },
        {
            "label": "edge: tool_count=2 (the weak-signal case)",
            "meta": {
                "tool_count": 2,
                "registry_source": "verified",
                "age_days": 400,
                "download_count": 5000,
                "dependency_count": 3,
                "publisher_verified": True,
                "stars": 150,
            },
        },
    ]

    results = []
    for tc in test_cases:
        score, ev = compute_score(tc["meta"])
        assert 0.0 <= score <= 100.0, f"score out of range: {score}"
        assert isinstance(ev, dict) and ev, "evidence dict empty"
        assert "fields_used" in ev and len(ev["fields_used"]) >= 5
        results.append((tc["label"], score))

    # Discrimination sanity: official/popular should beat community/sparse
    assert results[0][1] > results[1][1], (
        f"no discrimination: {results[0]} vs {results[1]}"
    )
    # Unknown-source case should still produce a valid bounded score
    assert 0.0 <= results[2][1] <= 100.0

    for label, score in results:
        print(f"  {label:50s} -> {score}")
    print("OK: tool_count_enrichment self-smoke passed.")
