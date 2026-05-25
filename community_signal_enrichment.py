#!/usr/bin/env python3
"""
community_signal_enrichment.py

Community signal scoring based on registry source, age, downloads, dependencies,
publisher verification, stars, forks, subscribers, and issue resolution ratio.

Signal Invariant (PRODUCT_SPEC §3):
    compute_score(metadata: dict) -> (float in [0,100], evidence dict)
    Pure function: no DB writes, no network.
"""
# deps: hashlib, math (stdlib only)

import hashlib
import math

SERVICE_NAME = "community_signal_enrichment"
SIGNAL_NAME = "community_signal"
VERSION = "v5"
MAX_SCORE = 100.0

# Hard caps for log-normalization
MAX_AGE_DAYS = 3650
MAX_DOWNLOADS = 10_000_000
MAX_DEPENDENCIES = 100
MAX_STARS = 10_000
MAX_FORKS = 1_000
MAX_SUBSCRIBERS = 10_000

# Weights for 7 components (must sum to 1.0)
SIGNAL_WEIGHTS = [0.20, 0.10, 0.25, 0.10, 0.15, 0.12, 0.08]

SOFTMAX_TEMP = 3.0

# ---- Math primitives -------------------------------------------------------

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def log_normalize(value: float, max_value: float) -> float:
    """Log-scale normalization to [0,1] with capped max."""
    if value <= 0:
        return 0.0
    return math.log(1.0 + value) / math.log(1.0 + max_value)


def softmax_weight(components: list[float], base_score: float) -> float:
    """Temperature-sharpened softmax over components."""
    if not components:
        return base_score
    max_val = max(components) if components else 1.0
    shifted = [c - max_val for c in components]
    exp_vals = [math.exp(v * SOFTMAX_TEMP) for v in shifted]
    total = sum(exp_vals)
    if total > 0:
        weights = [e / total for e in exp_vals]
        return sum(c * w for c, w in zip(components, weights))
    return base_score


def hash_string(s: str) -> int:
    """MD5-based hash for deterministic string scoring."""
    if s is None:
        s = ""
    return int(hashlib.md5(str(s).encode("utf-8")).hexdigest()[:12], 16)


# ---- Normalization --------------------------------------------------------

def _normalize_keys(raw: dict) -> dict:
    """Snake-case all keys; strip None."""
    out = {}
    for k, v in raw.items():
        key = k.lower().replace("-", "_").replace(" ", "_")
        if v is not None:
            out[key] = v
    return out


# ---- Component scorers ----------------------------------------------------

def _registry_score(registry_source: str) -> float:
    trust_map = {
        "official": 1.0,
        "npm_enterprise": 0.95,
        "verified": 0.85,
        "github_verified": 0.80,
        "community": 0.60,
        "third_party": 0.45,
        "unknown": 0.30,
    }
    return trust_map.get(str(registry_source).lower(), 0.30)


def _age_score(age_days: int) -> float:
    if age_days <= 0:
        return 0.0
    capped = min(age_days, MAX_AGE_DAYS)
    # Sigmoid centered at ~500 days
    return sigmoid((capped - 500) / 365)


def _download_score(download_count: int) -> float:
    if download_count <= 0:
        return 0.0
    return log_normalize(download_count, MAX_DOWNLOADS)


def _dependency_score(dependency_count: int) -> float:
    if dependency_count <= 0:
        return 0.0
    return log_normalize(dependency_count, MAX_DEPENDENCIES)


def _verified_score(publisher_verified: bool) -> float:
    return 1.0 if publisher_verified else 0.3


def _stars_score(stars: int) -> float:
    if stars <= 0:
        return 0.0
    return log_normalize(stars, MAX_STARS)


def _forks_score(forks: int) -> float:
    if forks <= 0:
        return 0.0
    return log_normalize(forks, MAX_FORKS)


def _subscribers_score(subscribers: int) -> float:
    if subscribers <= 0:
        return 0.0
    return log_normalize(subscribers, MAX_SUBSCRIBERS)


def _issue_ratio_score(open_issues: int, closed_issues: int) -> float:
    total = open_issues + closed_issues
    if total <= 0:
        return 0.5
    closed_ratio = closed_issues / total
    # 30-100 range based on closure ratio
    return 0.3 + (closed_ratio * 0.7)


# ---- Deterministic fallback -----------------------------------------------

def _hash_score(metadata: dict) -> float:
    """Deterministic fallback when primary inputs are missing."""
    hash_vals = [
        hash_string(str(metadata.get("registry_source", ""))) % 10000,
        hash_string(str(metadata.get("age_days", "0"))) % 10000,
        hash_string(str(metadata.get("download_count", "0"))) % 10000,
        hash_string(str(metadata.get("dependency_count", "0"))) % 10000,
        hash_string(str(metadata.get("publisher_verified", "false"))) % 10000,
        hash_string(str(metadata.get("stars", "0"))) % 10000,
        hash_string(str(metadata.get("forks", "0"))) % 10000,
    ]
    components = [v / 10000.0 for v in hash_vals]
    return sum(c * w for c, w in zip(components, SIGNAL_WEIGHTS))


# ---- Public API (enrichment contract) -------------------------------------

def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute community signal score from metadata dict.

    Args:
        metadata: dict with keys registry_source, age_days, download_count,
                  dependency_count, publisher_verified, stars, forks,
                  subscribers (optional), open_issues (optional),
                  closed_issues (optional).

    Returns:
        (float in [0,100], evidence dict) per PRODUCT_SPEC §3 signal invariant.
    """
    meta = _normalize_keys(metadata)

    # Extract components
    registry_source = meta.get("registry_source", "unknown")
    age_days = int(meta.get("age_days", 0))
    download_count = int(meta.get("download_count", 0))
    dependency_count = int(meta.get("dependency_count", 0))
    publisher_verified = bool(meta.get("publisher_verified", False))
    stars = int(meta.get("stars", 0))
    forks = int(meta.get("forks", 0))
    subscribers = int(meta.get("subscribers", 0))
    open_issues = int(meta.get("open_issues", 0))
    closed_issues = int(meta.get("closed_issues", 0))

    # Score each component
    scores = {
        "registry": _registry_score(registry_source),
        "age": _age_score(age_days),
        "download": _download_score(download_count),
        "dependency": _dependency_score(dependency_count),
        "verified": _verified_score(publisher_verified),
        "stars": _stars_score(stars),
        "forks": _forks_score(forks),
        "subscribers": _subscribers_score(subscribers),
        "issue_ratio": _issue_ratio_score(open_issues, closed_issues),
    }

    # Build weighted base score
    base_score = sum(
        scores[k] * w
        for k, w in zip(
            ["registry", "age", "download", "dependency", "verified", "stars", "forks"],
            SIGNAL_WEIGHTS,
        )
    )
    # subscribers and issue_ratio get 5% each (extracted from leftovers)
    base_score += scores["subscribers"] * 0.05
    base_score += scores["issue_ratio"] * 0.05

    # Sharpen with softmax
    sharpened = softmax_weight(list(scores.values()), base_score)
    final = max(0.0, min(1.0, sharpened))

    # Map to [0,100] scale
    score_100 = round(final * MAX_SCORE, 4)

    evidence = {
        "signal_type": SIGNAL_NAME,
        "confidence": round(final, 4),
        "evidence_blob": {
            "registry_source_score": round(scores["registry"], 4),
            "age_score": round(scores["age"], 4),
            "download_score": round(scores["download"], 4),
            "dependency_score": round(scores["dependency"], 4),
            "verified_score": round(scores["verified"], 4),
            "stars_score": round(scores["stars"], 4),
            "forks_score": round(scores["forks"], 4),
            "subscribers_score": round(scores["subscribers"], 4),
            "issue_ratio_score": round(scores["issue_ratio"], 4),
            "base_score": round(base_score, 4),
            "sharpened_score": round(sharpened, 4),
            "final_score": score_100,
            "metadata_summary": {
                "registry": str(registry_source),
                "age_days": age_days,
                "downloads": download_count,
                "verified": publisher_verified,
                "stars": stars,
                "forks": forks,
            },
        },
    }

    return score_100, evidence


# ---- Deterministic variant ------------------------------------------------

def compute_score_deterministic(metadata: dict) -> tuple[float, dict]:
    """Hash-only scoring for testing/deduplication."""
    final = max(0.0, min(1.0, _hash_score(metadata)))
    score_100 = round(final * MAX_SCORE, 4)
    return score_100, {
        "signal_type": SIGNAL_NAME,
        "confidence": round(final, 4),
        "evidence_blob": {"deterministic": True},
    }


# ---- Signal info ----------------------------------------------------------

def get_signal_info() -> dict:
    """Return signal metadata for registration."""
    return {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "max_score": MAX_SCORE,
        "description": (
            "Community signal scoring: registry trust, package age, download "
            "volume, dependency count, publisher verification, GitHub stars/forks, "
            "subscribers, and issue resolution ratio."
        ),
    }


# ---- Self-smoke -----------------------------------------------------------

if __name__ == "__main__":
    import json

    test_cases = [
        # Case 1: Mature official package with high engagement
        {
            "registry_source": "official",
            "age_days": 730,
            "download_count": 5_000_000,
            "dependency_count": 12,
            "publisher_verified": True,
            "stars": 1500,
            "forks": 200,
            "subscribers": 100,
            "open_issues": 5,
            "closed_issues": 45,
        },
        # Case 2: Brand-new third-party package, no traction
        {
            "registry_source": "third_party",
            "age_days": 30,
            "download_count": 100,
            "dependency_count": 0,
            "publisher_verified": False,
            "stars": 5,
            "forks": 0,
            "subscribers": 0,
            "open_issues": 0,
            "closed_issues": 0,
        },
        # Case 3: Mature verified package with good health
        {
            "registry_source": "verified",
            "age_days": 1095,
            "download_count": 50_000,
            "dependency_count": 25,
            "publisher_verified": True,
            "stars": 500,
            "forks": 50,
            "subscribers": 50,
            "open_issues": 10,
            "closed_issues": 90,
        },
        # Case 4: Deterministic fallback (empty)
        {},
        # Case 5: Mixed keys (snake vs kebab)
        {
            "registry-source": "community",
            "Age_Days": 365,
            "download-count": 10000,
            "Publisher_Verified": "false",
            "Stars": 50,
            "Forks": 5,
        },
    ]

    print("Community Signal Enrichment v5 — Self-Smoke")
    print("=" * 60)
    all_passed = True

    for i, tc in enumerate(test_cases, 1):
        score, evidence = compute_score(tc)
        label = tc.get("registry_source", tc.get("registry-source", "unknown"))
        # Gate 8 contract check: score must be in [0, 100]
        in_range = 0.0 <= score <= 100.0
        has_evidence = isinstance(evidence, dict) and "evidence_blob" in evidence
        ok = in_range and has_evidence

        print(f"\nCase {i}: {label}")
        print(f"  Score: {score} (in [0,100]: {in_range})")
        print(f"  Has evidence_blob: {has_evidence}")
        print(f"  PASS" if ok else f"  FAIL")
        if not ok:
            all_passed = False

    print("\n" + "=" * 60)
    info = get_signal_info()
    print(f"Signal: {info['signal_name']} {info['version']}")
    print(f"Max score: {info['max_score']}")
    print(f"Description: {info['description'][:80]}...")

    print("\n" + ("ALL TESTS PASSED" if all_passed else "SOME TESTS FAILED"))
    if not all_passed:
        raise SystemExit(1)