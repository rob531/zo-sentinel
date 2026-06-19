"""
tool_count_enrichment_v4.py

Enrichment module that addresses the weak signal for tool_count (only 2 distinct
values in the 55‑92 range) by incorporating additional metadata fields into a
bucketed scoring system.  The score is computed using a pure function with no
side‑effects or DB writes.

Public API
-----------
compute_score(metadata: dict) -> tuple[float, dict]
    Takes a dictionary containing any of the following keys:
        - registry_source (str)
        - age_days (int or float)
        - publisher_verified (bool)
        - stars (int or float)
        - download_count (int or float)
        - dependency_count (int or float)
        - tool_description_safety_score (float)
        - tool_count (int or float)

    Returns a tuple:
        - float: final normalized score (0‑100, two decimal places)
        - dict: detailed breakdown with bucket indices, component scores,
                raw weighted sum, maximum possible score, and the method name.
"""

from typing import Any, Dict, Tuple

# ----------------------------------------------------------------------
# Bucket thresholds (inclusive upper bound) for numeric fields
# ----------------------------------------------------------------------
AGE_DAYS_THRESHOLDS = [0, 30, 90, 180, 365, 730, 1095, 1825, 2555, 3650]

STARS_THRESHOLDS = [0, 5, 20, 50, 100, 500, 1000, 5000, 10000]

DOWNLOAD_COUNT_THRESHOLDS = [
    0,
    100,
    1_000,
    10_000,
    100_000,
    500_000,
    1_000_000,
    5_000_000,
    10_000_000,
]

DEPENDENCY_COUNT_THRESHOLDS = [0, 5, 10, 20, 50, 100, 200]

SAFETY_SCORE_THRESHOLDS = [
    0.0,
    0.1,
    0.2,
    0.3,
    0.4,
    0.5,
    0.6,
    0.7,
    0.8,
    0.9,
    1.0,
]

TOOL_COUNT_THRESHOLDS = [
    0,
    10,
    20,
    30,
    40,
    50,
    60,
    70,
    80,
    90,
    100,
]

# ----------------------------------------------------------------------
# Mapping for categorical field
# ----------------------------------------------------------------------
REGISTRY_SOURCE_MAPPING: Dict[str, int] = {
    "pypi": 0,
    "npm": 1,
    "maven": 2,
    "nuget": 3,
    "conda": 4,
    "rubygems": 5,
    "packagist": 6,
    "go": 7,
    "cargo": 8,
}

# ----------------------------------------------------------------------
# Component weights (tuneable)
# ----------------------------------------------------------------------
WEIGHTS: Dict[str, float] = {
    "registry_source": 1.0,
    "age_days": 2.0,
    "publisher_verified": 1.5,
    "stars": 1.0,
    "download_count": 1.0,
    "dependency_count": 0.5,
    "tool_description_safety_score": 2.0,
    "tool_count": 1.0,
}

# ----------------------------------------------------------------------
# Helper: bucket a numeric value
# ----------------------------------------------------------------------
def _get_bucket(value: Any, thresholds: list) -> int:
    """Return the index of the first threshold that is >= value.
    If value is None, return 0 (neutral bucket).
    If value exceeds all thresholds, return len(thresholds).
    """
    if value is None:
        return 0
    for i, thresh in enumerate(thresholds):
        if value <= thresh:
            return i
    return len(thresholds)


# ----------------------------------------------------------------------
# Main scoring function
# ----------------------------------------------------------------------
def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute a normalized bucketed score from the supplied metadata.

    Parameters
    ----------
    metadata : dict
        Dictionary containing tool metadata fields. Missing fields are
        treated as neutral (bucket index 0).

    Returns
    -------
    tuple[float, dict]
        - final_score (float): normalized score in the range 0‑100.
        - details (dict): breakdown of bucket indices, component scores,
          raw weighted sum, max possible score, and method description.
    """
    # ------------------------------------------------------------------
    # Bucket each field
    # ------------------------------------------------------------------
    bucket_indices: Dict[str, int] = {}
    component_scores: Dict[str, float] = {}

    # registry_source (categorical → integer index)
    rs = metadata.get("registry_source")
    rs_idx = REGISTRY_SOURCE_MAPPING.get(rs, 0) if rs else 0
    bucket_indices["registry_source"] = rs_idx
    component_scores["registry_source"] = rs_idx * WEIGHTS["registry_source"]

    # age_days
    age_idx = _get_bucket(metadata.get("age_days"), AGE_DAYS_THRESHOLDS)
    bucket_indices["age_days"] = age_idx
    component_scores["age_days"] = age_idx * WEIGHTS["age_days"]

    # publisher_verified (bool → 0/1)
    pv = metadata.get("publisher_verified")
    pv_idx = 1 if pv else 0
    bucket_indices["publisher_verified"] = pv_idx
    component_scores["publisher_verified"] = pv_idx * WEIGHTS["publisher_verified"]

    # stars
    stars_idx = _get_bucket(metadata.get("stars"), STARS_THRESHOLDS)
    bucket_indices["stars"] = stars_idx
    component_scores["stars"] = stars_idx * WEIGHTS["stars"]

    # download_count
    dl_idx = _get_bucket(metadata.get("download_count"), DOWNLOAD_COUNT_THRESHOLDS)
    bucket_indices["download_count"] = dl_idx
    component_scores["download_count"] = dl_idx * WEIGHTS["download_count"]

    # dependency_count
    dep_idx = _get_bucket(metadata.get("dependency_count"), DEPENDENCY_COUNT_THRESHOLDS)
    bucket_indices["dependency_count"] = dep_idx
    component_scores["dependency_count"] = dep_idx * WEIGHTS["dependency_count"]

    # tool_description_safety_score
    safety_idx = _get_bucket(
        metadata.get("tool_description_safety_score"), SAFETY_SCORE_THRESHOLDS
    )
    bucket_indices["tool_description_safety_score"] = safety_idx
    component_scores["tool_description_safety_score"] = (
        safety_idx * WEIGHTS["tool_description_safety_score"]
    )

    # tool_count (original weak‑signal field)
    tc_idx = _get_bucket(metadata.get("tool_count"), TOOL_COUNT_THRESHOLDS)
    bucket_indices["tool_count"] = tc_idx
    component_scores["tool_count"] = tc_idx * WEIGHTS["tool_count"]

    # ------------------------------------------------------------------
    # Weighted sum and normalization
    # ------------------------------------------------------------------
    raw_weighted_sum = sum(component_scores.values())

    # Maximum possible bucket indices for each component (for normalization)
    max_bucket_indices: Dict[str, int] = {
        "registry_source": len(REGISTRY_SOURCE_MAPPING) - 1,
        "age_days": len(AGE_DAYS_THRESHOLDS),
        "publisher_verified": 1,
        "stars": len(STARS_THRESHOLDS),
        "download_count": len(DOWNLOAD_COUNT_THRESHOLDS),
        "dependency_count": len(DEPENDENCY_COUNT_THRESHOLDS),
        "tool_description_safety_score": len(SAFETY_SCORE_THRESHOLDS),
        "tool_count": len(TOOL_COUNT_THRESHOLDS),
    }

    max_possible_score = sum(
        max_idx * WEIGHTS[field] for field, max_idx in max_bucket_indices.items()
    )

    if max_possible_score > 0:
        normalized = (raw_weighted_sum / max_possible_score) * 100.0
    else:
        normalized = 0.0

    final_score = round(normalized, 2)

    # ------------------------------------------------------------------
    # Assemble result dictionary
    # ------------------------------------------------------------------
    details: Dict[str, Any] = {
        "bucket_indices": bucket_indices,
        "component_scores": component_scores,
        "raw_weighted_sum": raw_weighted_sum,
        "max_possible_score": max_possible_score,
        "final_score": final_score,
        "method": (
            "bucketed weighted sum with normalization to 0‑100; "
            "uses registry_source, age_days, publisher_verified, stars, "
            "download_count, dependency_count, tool_description_safety_score, "
            "and tool_count"
        ),
    }

    return final_score, details