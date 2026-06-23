# deps: 
"""
Description Safety Signal Enrichment
---------------------------------
This enrichment evaluates the safety and quality of a tool's description and
metadata. It reads a set of fields from the supplied ``metadata`` dict and returns
a score in the range ``0``‑``100`` together with an ``evidence`` dictionary that
contains the intermediate component scores.

The contract required by ``enrichment_harness.py`` is:

    compute_score(metadata: dict) -> (float, dict)

The function must be pure – no network calls, no DB writes, and no imports of
forbidden modules.
"""
from __future__ import annotations
import re
from typing import Any, Dict, Tuple

# ---------------------------------------------------------------------------
# Helper scoring functions
# ---------------------------------------------------------------------------

def _get(metadata: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Case‑insensitive key lookup with a fallback.
    ``metadata`` may contain keys in different capitalisations; this helper
    normalises the lookup.
    """
    for k in metadata:
        if k.lower() == key.lower():
            return metadata[k]
    return default


def _score_description_quality(desc: str) -> float:
    """Score based on length and clarity.
    Returns a value in ``[0, 1]``.
    """
    if not isinstance(desc, str) or not desc.strip():
        return 0.0
    length = len(desc)
    # Length component – favour concise but informative texts
    if length < 30:
        length_score = 0.2
    elif length < 70:
        length_score = 0.5
    elif length < 150:
        length_score = 0.8
    else:
        length_score = 0.6
    # Clarity component – penalise vague placeholders
    vague_patterns = [
        r"^\s*$",
        r"^none$",
        r"^n/a$",
        r"^todo$",
        r"^tbd$",
        r"^see documentation$",
        r"^see docs$",
        r"^readme$",
    ]
    lower = desc.strip().lower()
    if any(re.fullmatch(p, lower) for p in vague_patterns):
        clarity_score = 0.0
    else:
        clarity_score = 0.9
    return (length_score + clarity_score) / 2.0


def _score_dependency_safety(dep_count: int) -> float:
    """Higher dependency counts are riskier.
    ``dep_count`` is interpreted as an integer; non‑numeric values default to 0.
    """
    if dep_count <= 0:
        return 1.0
    if dep_count <= 5:
        return 0.9
    if dep_count <= 15:
        return 0.7
    if dep_count <= 30:
        return 0.5
    if dep_count <= 50:
        return 0.3
    return 0.1


def _score_publisher_trust(verified: bool, stars: int, downloads: int) -> float:
    """Combine publisher verification, star count and download count.
    Returns a value in ``[0, 1]``.
    """
    score = 0.4  # baseline
    if verified:
        score += 0.3
    if stars >= 1000:
        score += 0.15
    if downloads >= 100_000:
        score += 0.1
    return min(score, 1.0)


def _score_age_trust(age_days: int) -> float:
    """Older packages are considered more trustworthy.
    ``age_days`` may be missing; treat missing as 0.
    """
    if age_days < 7:
        return 0.3
    if age_days < 30:
        return 0.5
    if age_days < 90:
        return 0.7
    if age_days < 365:
        return 0.85
    return 1.0


def _score_registry_source(source: str) -> float:
    """Trusted registries receive a higher score.
    The list mirrors ``TRUSTED_REGISTRIES`` used elsewhere in the codebase.
    """
    trusted = {"npmjs", "npm_official", "pypi", "github", "smithery"}
    if not isinstance(source, str):
        return 0.2
    return 1.0 if source.lower() in trusted else 0.4

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Pure enrichment function.

    Parameters
    ----------
    metadata: dict
        Expected keys (case‑insensitive)::

            registry_source, age_days, download_count, dependency_count,
            publisher_verified, stars, description, tool_description

    Returns
    -------
    (float, dict)
        * ``float`` – score in the inclusive range ``0``‑``100``.
        * ``dict`` – evidence containing the component scores.
    """
    # -------------------------------------------------------------------
    # Extract fields with sensible defaults
    # -------------------------------------------------------------------
    registry_source = _get(metadata, "registry_source", "unknown")
    age_days = _get(metadata, "age_days", 0)
    download_count = _get(metadata, "download_count", 0)
    dependency_count = _get(metadata, "dependency_count", 0)
    publisher_verified = _get(metadata, "publisher_verified", False)
    stars = _get(metadata, "stars", 0)
    # Description may be stored under a few alternative keys
    description = _get(metadata, "description", "") or _get(metadata, "tool_description", "")

    # Normalise numeric fields – tolerate strings
    try:
        age_days = int(age_days)
    except Exception:
        age_days = 0
    try:
        download_count = int(download_count)
    except Exception:
        download_count = 0
    try:
        dependency_count = int(dependency_count)
    except Exception:
        dependency_count = 0
    try:
        stars = int(stars)
    except Exception:
        stars = 0
    publisher_verified = bool(publisher_verified)

    # -------------------------------------------------------------------
    # Compute component scores (all in [0, 1])
    # -------------------------------------------------------------------
    desc_score = _score_description_quality(description)
    dep_score = _score_dependency_safety(dependency_count)
    pub_score = _score_publisher_trust(publisher_verified, stars, download_count)
    age_score = _score_age_trust(age_days)
    reg_score = _score_registry_source(registry_source)

    # Weighted aggregation – weights sum to 1.0
    weights = {
        "desc": 0.30,
        "dep": 0.20,
        "pub": 0.20,
        "age": 0.15,
        "reg": 0.15,
    }
    final_score = (
        desc_score * weights["desc"]
        + dep_score * weights["dep"]
        + pub_score * weights["pub"]
        + age_score * weights["age"]
        + reg_score * weights["reg"]
    )
    # Scale to 0‑100 and round to two decimals for reproducibility
    final_score = round(final_score * 100, 2)

    evidence: Dict[str, Any] = {
        "description_quality": desc_score,
        "dependency_safety": dep_score,
        "publisher_trust": pub_score,
        "age_trust": age_score,
        "registry_source_trust": reg_score,
        "raw_score": final_score,
    }
    return final_score, evidence

# ---------------------------------------------------------------------------
# Self‑smoke test (executed when run as a script)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Three representative inputs covering low, medium and high risk
    test_cases = [
        {
            "registry_source": "npm_official",
            "age_days": 10,
            "download_count": 50,
            "dependency_count": 2,
            "publisher_verified": False,
            "stars": 10,
            "description": "A tiny utility that formats JSON.",
        },
        {
            "registry_source": "github",
            "age_days": 200,
            "download_count": 50000,
            "dependency_count": 12,
            "publisher_verified": True,
            "stars": 1500,
            "description": "A well‑documented library for image processing with examples and clear API reference.",
        },
        {
            "registry_source": "unknown",
            "age_days": 2,
            "download_count": 0,
            "dependency_count": 60,
            "publisher_verified": False,
            "stars": 0,
            "description": "TODO",
        },
    ]
    for i, meta in enumerate(test_cases, 1):
        score, ev = compute_score(meta)
        assert 0.0 <= score <= 100.0, f"Score out of bounds in case {i}"
        assert isinstance(ev, dict) and ev, f"Evidence missing in case {i}"
        print(f"Case {i}: score={score}, evidence={ev}")
    print("Self‑test passed.")
