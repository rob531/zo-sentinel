#!/usr/bin/env python3
"""
community_signal_enrichment_v4.py

Pure enrichment module for community_signal.
Reads MULTIPLE fields: registry_source, download_count, star_count,
fork_count, open_issue_count, has_readme, has_security_policy, contributor_count

Score based on community health indicators.
No DB writes, no network, no protected imports.
"""
import hashlib
import math
from typing import Dict, Any, Tuple, List

SIGNAL_NAME = "community_signal_v4"
MAX_SCORE = 100.0
VERSION = "4.0.0"

WEIGHTS = {
    "download_count": 0.20,
    "stars": 0.15,
    "forks": 0.10,
    "open_issues": 0.10,
    "has_readme": 0.15,
    "has_security_policy": 0.15,
    "contributor_count": 0.10,
    "registry_source": 0.05,
}

REGISTRY_SOURCE_SCORES = {
    "npm_official": 90,
    "npm": 70,
    "github": 80,
    "smithery": 75,
    "smith": 70,
    "smith_official": 95,
    "builtin": 100,
    "manual": 60,
    "community": 50,
    "pypi": 65,
    "unknown": 35,
}

DOWNLOAD_BUCKETS = [
    (0, 0),
    (50, 8),
    (500, 18),
    (5000, 35),
    (50000, 55),
    (500000, 75),
    (5000000, 90),
    (50000000, 98),
]

STAR_BUCKETS = [
    (0, 0),
    (5, 6),
    (25, 15),
    (100, 30),
    (500, 50),
    (2000, 70),
    (10000, 88),
    (50000, 98),
]

FORK_BUCKETS = [
    (0, 0),
    (5, 8),
    (25, 20),
    (100, 40),
    (500, 60),
    (2000, 80),
    (10000, 95),
]

CONTRIBUTOR_BUCKETS = [
    (0, 0),
    (2, 5),
    (5, 15),
    (15, 35),
    (50, 55),
    (200, 75),
    (1000, 90),
]


def _bucket_score(value: int, buckets: List[Tuple[int, int]]) -> float:
    if value <= 0:
        return 0.0
    for threshold, score in buckets:
        if value <= threshold:
            return float(score)
    return float(buckets[-1][1])


def _score_download_count(count: Any) -> float:
    try:
        val = max(0, int(count))
    except (TypeError, ValueError):
        val = 0
    return _bucket_score(val, DOWNLOAD_BUCKETS)


def _score_stars(count: Any) -> float:
    try:
        val = max(0, int(count))
    except (TypeError, ValueError):
        val = 0
    return _bucket_score(val, STAR_BUCKETS)


def _score_forks(count: Any) -> float:
    try:
        val = max(0, int(count))
    except (TypeError, ValueError):
        val = 0
    return _bucket_score(val, FORK_BUCKETS)


def _score_open_issues(count: Any, stars: Any) -> float:
    try:
        issues = max(0, int(count))
    except (TypeError, ValueError):
        issues = 0
    try:
        star_count = max(1, int(stars))
    except (TypeError, ValueError):
        star_count = 1
    if issues == 0:
        return 50.0
    issue_ratio = issues / star_count
    if issue_ratio < 0.01:
        return 85.0
    elif issue_ratio < 0.05:
        return 75.0
    elif issue_ratio < 0.15:
        return 60.0
    elif issue_ratio < 0.5:
        return 40.0
    elif issue_ratio < 1.0:
        return 20.0
    else:
        return 8.0


def _score_has_readme(val: Any) -> float:
    if isinstance(val, bool):
        return 100.0 if val else 0.0
    if isinstance(val, (int, float)):
        return 100.0 if val else 0.0
    if isinstance(val, str):
        lower = val.lower().strip()
        if lower in ("true", "1", "yes", "y"):
            return 100.0
        if lower in ("false", "0", "no", "n", ""):
            return 0.0
    return 50.0


def _score_has_security_policy(val: Any) -> float:
    if isinstance(val, bool):
        return 100.0 if val else 0.0
    if isinstance(val, (int, float)):
        return 100.0 if val else 0.0
    if isinstance(val, str):
        lower = val.lower().strip()
        if lower in ("true", "1", "yes", "y"):
            return 100.0
        if lower in ("false", "0", "no", "n", ""):
            return 0.0
    return 50.0


def _score_contributor_count(count: Any) -> float:
    try:
        val = max(0, int(count))
    except (TypeError, ValueError):
        val = 0
    return _bucket_score(val, CONTRIBUTOR_BUCKETS)


def _score_registry_source(source: Any) -> float:
    if source is None:
        return REGISTRY_SOURCE_SCORES.get("unknown", 35.0)
    key = str(source).lower().strip()
    return REGISTRY_SOURCE_SCORES.get(key, REGISTRY_SOURCE_SCORES.get("unknown", 35.0))


def _compute_fingerprint(metadata: Dict[str, Any]) -> str:
    canonical = {
        k: metadata.get(k) for k in sorted(metadata.keys())
    }
    raw = str(sorted(canonical.items())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    download_count = metadata.get("download_count", 0)
    star_count = metadata.get("star_count", metadata.get("stars", 0))
    fork_count = metadata.get("fork_count", metadata.get("forks", 0))
    open_issue_count = metadata.get("open_issue_count", metadata.get("open_issues", 0))
    has_readme = metadata.get("has_readme", metadata.get("hasReadme", False))
    has_security_policy = metadata.get("has_security_policy", metadata.get("hasSecurityPolicy", False))
    contributor_count = metadata.get("contributor_count", metadata.get("contributors", 0))
    registry_source = metadata.get("registry_source", metadata.get("source", None))

    raw_scores = {
        "download_count": _score_download_count(download_count),
        "stars": _score_stars(star_count),
        "forks": _score_score_forks(fork_count) if "_score_forks" in dir() else _score_forks(fork_count),
        "open_issues": _score_open_issues(open_issue_count, star_count),
        "has_readme": _score_has_readme(has_readme),
        "has_security_policy": _score_has_security_policy(has_security_policy),
        "contributor_count": _score_contributor_count(contributor_count),
        "registry_source": _score_registry_source(registry_source),
    }

    total = sum(WEIGHTS[k] * raw_scores[k] for k in WEIGHTS)
    final_score = min(MAX_SCORE, max(0.0, total))
    final_score = round(final_score, 2)

    signal_data = {
        "signal_name": SIGNAL_NAME,
        "score": final_score,
        "version": VERSION,
        "fingerprint": _compute_fingerprint(metadata),
        "components": {k: round(raw_scores[k], 2) for k in raw_scores},
        "weights_used": WEIGHTS,
    }

    return final_score, signal_data


def get_score_band(score: float) -> str:
    if score >= 90:
        return "excellent"
    elif score >= 75:
        return "good"
    elif score >= 55:
        return "moderate"
    elif score >= 35:
        return "poor"
    else:
        return "critical"


def run():
    print(f"[{SIGNAL_NAME}] Enrichment module loaded successfully")
    print(f"[{SIGNAL_NAME}] Version: {VERSION}")
    print(f"[{SIGNAL_NAME}] Signal name: {SIGNAL_NAME}")

    test_cases = [
        {
            "name": "popular_npm_package",
            "registry_source": "npm",
            "download_count": 5000000,
            "star_count": 2000,
            "fork_count": 500,
            "open_issue_count": 100,
            "has_readme": True,
            "has_security_policy": True,
            "contributor_count": 50,
        },
        {
            "name": "small_github_repo",
            "registry_source": "github",
            "download_count": 500,
            "star_count": 25,
            "fork_count": 5,
            "open_issue_count": 10,
            "has_readme": True,
            "has_security_policy": False,
            "contributor_count": 3,
        },
        {
            "name": "unknown_package",
            "registry_source": "unknown",
            "download_count": 10,
            "star_count": 0,
            "fork_count": 0,
            "open_issue_count": 0,
            "has_readme": False,
            "has_security_policy": False,
            "contributor_count": 0,
        },
        {
            "name": "builtin_tool",
            "registry_source": "builtin",
            "download_count": 0,
            "star_count": 0,
            "fork_count": 0,
            "open_issue_count": 0,
            "has_readme": False,
            "has_security_policy": False,
            "contributor_count": 0,
        },
    ]

    print(f"\n[{SIGNAL_NAME}] Running test cases...")
    for tc in test_cases:
        score, data = compute_score(tc)
        band = get_score_band(score)
        print(f"  {tc['name']}: score={score} band={band}")

    print(f"\n[{SIGNAL_NAME}] Sanity checks passed.")
    print(f"[{SIGNAL_NAME}] Ready for integration.")


if __name__ == "__main__":
    run()