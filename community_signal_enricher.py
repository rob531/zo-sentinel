#!/usr/bin/env python3
"""
community_signal_enricher.py

Pure enrichment module for the community_signal dimension (0–100).

Signal Invariant (PRODUCT_SPEC §3):
    compute_score(metadata: dict) -> (float in [0,100], evidence dict)
    Pure function: no DB writes, no network, no imports of protected modules.

Inputs (all optional; missing fields contribute 0 and are listed in evidence['missing']):
    github_stars         (int)
    github_forks         (int)
    github_open_issues   (int)
    npm_downloads        (int)
    pypi_downloads       (int)
    registry_source      (str: 'npm'|'pypi'|'github'|'smithery'|'other')
    last_commit_days     (int)
    maintainer_count     (int)
    readme_length_chars  (int)

Scoring formula:
    stars_fraction     0–100  log-norm to 5000 stars = 100
    download_fraction  0–100  log-norm to 1 000 000 downloads = 100
    recency_bonus      0–15   linear decay over 365 days (last_commit_days)
    diversity_bonus    0–10   based on maintainer_count
    docs_quality       0–10   linear over 2000 chars readme_length_chars
    Final score capped at 100; missing fields contribute 0 and are appended
    to evidence['missing'].
"""

import math

__version__ = "v1"


# ---- Helpers ---------------------------------------------------------------

def _norm_stars(stars: int) -> float:
    """0–100: log-norm, 5000 stars → 100."""
    if stars <= 0:
        return 0.0
    # log(1+5000)/log(1+5000) = 1 → 100; lower stars score proportionally
    return min(100.0, math.log1p(stars) / math.log1p(5_000) * 100.0)


def _norm_downloads(downloads: int) -> float:
    """0–100: log-norm, 1M downloads → 100."""
    if downloads <= 0:
        return 0.0
    return min(100.0, math.log1p(downloads) / math.log1p(1_000_000) * 100.0)


def _recency_bonus(last_commit_days: int) -> float:
    """0–15: linear decay to 0 over 365 days."""
    if last_commit_days < 0:
        return 0.0
    return max(0.0, 15.0 * max(0.0, 1.0 - last_commit_days / 365.0))


def _diversity_bonus(maintainer_count: int) -> float:
    """0–10: 1 maintainer → 0, ≥6 → 10, logarithmic ramp in between."""
    if maintainer_count <= 0:
        return 0.0
    if maintainer_count >= 6:
        return 10.0
    # log-scale: log(6)/log(6) = 1 → 10
    return round(10.0 * math.log(maintainer_count) / math.log(6.0), 4)


def _docs_quality(readme_length_chars: int) -> float:
    """0–10: linear over 2000 chars; cap at 10."""
    if readme_length_chars <= 0:
        return 0.0
    return min(10.0, readme_length_chars / 2000.0 * 10.0)


def _verdict(score: float) -> str:
    """Human-readable verdict label."""
    if score >= 80:
        return "high_community_trust"
    if score >= 50:
        return "moderate_community_presence"
    if score >= 20:
        return "limited_community_signals"
    return "minimal_community_footprint"


# ---- Public API ------------------------------------------------------------

def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute community_signal score from a metadata dict.

    Returns
        (score: float in [0, 100], evidence: dict)
    where evidence has:
        verdict     – str label
        missing     – list of missing field names
        components  – dict of sub-scores
    """
    # Track which fields are absent
    missing: list[str] = []

    def get_int(key: str) -> int:
        v = metadata.get(key)
        if v is None:
            missing.append(key)
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            missing.append(key)
            return 0

    github_stars        = get_int("github_stars")
    github_forks        = get_int("github_forks")
    github_open_issues  = get_int("github_open_issues")
    npm_downloads       = get_int("npm_downloads")
    pypi_downloads       = get_int("pypi_downloads")
    last_commit_days    = get_int("last_commit_days")
    maintainer_count    = get_int("maintainer_count")
    readme_length_chars = get_int("readme_length_chars")

    registry_source = metadata.get("registry_source")
    if registry_source is None:
        missing.append("registry_source")

    # ---- Component scores -------------------------------------------------
    stars_comp   = _norm_stars(github_stars)
    forks_comp    = _norm_stars(github_forks)          # reuse same log-norm scale
    # combine stars + forks into a single "popularity" component capped at 100
    pop_fraction  = min(100.0, stars_comp * 0.7 + forks_comp * 0.3)

    # download fraction: combine npm + pypi, cap at 1M total
    total_downloads = npm_downloads + pypi_downloads
    dl_fraction     = _norm_downloads(total_downloads)

    recency      = _recency_bonus(last_commit_days)
    diversity    = _diversity_bonus(maintainer_count)
    docs         = _docs_quality(readme_length_chars)

    # ---- Weighted total --------------------------------------------------
    # Weights chosen so components sum to ≤ 100
    raw = (
        pop_fraction * 0.30 +
        dl_fraction  * 0.30 +
        recency      * 0.15 +
        diversity    * 0.10 +
        docs         * 0.10
    )

    score = round(min(100.0, raw), 2)

    # ---- Evidence --------------------------------------------------------
    verdict_label = _verdict(score)
    components = {
        "stars_fraction":    round(stars_comp,  4),
        "forks_fraction":    round(forks_comp,  4),
        "popularity_score":  round(pop_fraction, 4),
        "download_fraction": round(dl_fraction,  4),
        "recency_bonus":     round(recency,      4),
        "diversity_bonus":   round(diversity,    4),
        "docs_quality":      round(docs,         4),
        "raw_score":         round(raw,          4),
    }

    evidence = {
        "verdict":     verdict_label,
        "missing":     missing,
        "components":  components,
    }

    return score, evidence


# ---- Self-smoke ------------------------------------------------------------

if __name__ == "__main__":
    # Acceptance criteria from the task:
    # 1. github_stars=5000, npm_downloads=100000, registry_source='npm' → score ≥ 0
    s1, e1 = compute_score({
        "github_stars":    5000,
        "npm_downloads":   100_000,
        "registry_source": "npm",
    })
    assert s1 >= 0, f"case1 score {s1} must be ≥ 0"

    # 2. all-missing path → 0 ≤ score ≤ 100
    s2, e2 = compute_score({})
    assert 0 <= s2 <= 100, f"case2 score {s2} outside [0,100]"

    # 3. github-only path → verdict key present
    s3, e3 = compute_score({"registry_source": "github"})
    assert "verdict" in e3, f"case3 missing verdict key: {e3.keys()}"

    print("PASS")
