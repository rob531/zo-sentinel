#!/usr/bin/env python3
"""
verify_community_signal_enrichment_quality.py

Quality verification module for community_signal_enrichment.py.

Exercises compute_score() against a synthetic corpus of 34 fingerprints
with intentionally varied metadata to verify discrimination capability.

Checks:
  - Return type is (float, dict)
  - Float score is always in [0.0, 100.0]
  - At least 20 distinct score values across the corpus
  - Prints discrimination statistics

This is NOT a daemon — it is an offline test script that exits 0 on success.
"""
# deps: json (stdlib only)

import json
from typing import Any

# Import the module under test (no side-effects — pure function)
from community_signal_enrichment import compute_score, get_signal_info

# ---- Synthetic corpus builder ---------------------------------------------

def _make_fingerprints() -> list[dict[str, Any]]:
    """
    Generate 34 fingerprints with deliberately varied metadata to stress
    the scoring pipeline and expose discrimination capability.
    """
    corpus = []

    # Registry sources (7 distinct)
    registries = [
        "official",
        "npm_enterprise",
        "verified",
        "github_verified",
        "community",
        "third_party",
        "unknown",
    ]

    # Ages in days (5 distinct tiers)
    age_days = [7, 90, 365, 730, 1825]

    # Download counts (log-spaced)
    downloads = [0, 10, 500, 5000, 100_000, 2_000_000, 8_000_000]

    # Dependency counts
    deps = [0, 1, 5, 20, 80]

    # Stars / forks (log-spaced)
    stars = [0, 5, 100, 1000, 5000]
    forks = [0, 1, 10, 100, 500]

    # Subscribers
    subs = [0, 5, 50, 200]

    # Issue resolution combos
    issue_combos = [
        (0, 0),
        (1, 9),
        (5, 45),
        (20, 80),
        (100, 400),
    ]

    # Publisher verified
    verified = [True, False]

    idx = 0
    # Exhaustively combine key dimensions to hit >= 34 cases
    for reg in registries:
        for age in age_days[:3]:        # 7 * 3 = 21
            for dl in downloads[:2]:   # 21 * 2 = 42 (cap at 34)
                if idx >= 34:
                    break
                corpus.append({
                    "registry_source": reg,
                    "age_days": age,
                    "download_count": dl,
                    "dependency_count": deps[idx % len(deps)],
                    "publisher_verified": verified[idx % 2],
                    "stars": stars[idx % len(stars)],
                    "forks": forks[idx % len(forks)],
                    "subscribers": subs[idx % len(subs)],
                    "open_issues": issue_combos[idx % len(issue_combos)][0],
                    "closed_issues": issue_combos[idx % len(issue_combos)][1],
                })
                idx += 1
                if idx >= 34:
                    break
            if idx >= 34:
                break
        if idx >= 34:
            break

    # Ensure exactly 34 entries by trimming or padding
    return corpus[:34]


# ---- Verifier -------------------------------------------------------------

def verify_enrichment_quality() -> dict[str, Any]:
    """
    Run all quality checks and return a results dict.
    Raises AssertionError on failure (caught by main).
    """
    signal_info = get_signal_info()
    corpus = _make_fingerprints()

    assert len(corpus) >= 34, (
        f"Corpus size {len(corpus)} < 34 — cannot reliably measure discrimination"
    )

    scores: list[float] = []
    evidence_list: list[dict] = []
    failures: list[str] = []

    for i, fingerprint in enumerate(corpus):
        try:
            result = compute_score(fingerprint)
        except Exception as exc:
            failures.append(f"Case {i}: compute_score raised {type(exc).__name__}: {exc}")
            continue

        # Type check
        if not isinstance(result, tuple):
            failures.append(f"Case {i}: expected tuple, got {type(result).__name__}")
            continue
        if len(result) != 2:
            failures.append(f"Case {i}: tuple length {len(result)} != 2")
            continue

        score_float, evidence = result

        if not isinstance(score_float, (int, float)):
            failures.append(
                f"Case {i}: score is {type(score_float).__name__}, not float"
            )
            continue

        if not isinstance(evidence, dict):
            failures.append(
                f"Case {i}: evidence is {type(evidence).__name__}, not dict"
            )
            continue

        if not (0.0 <= score_float <= 100.0):
            failures.append(
                f"Case {i}: score {score_float} outside [0.0, 100.0]"
            )
            continue

        scores.append(score_float)
        evidence_list.append(evidence)

    # ---- Discrimination check
    distinct_scores = len(set(round(s, 4) for s in scores))
    min_score = min(scores) if scores else None
    max_score = max(scores) if scores else None
    span = (max_score - min_score) if (min_score is not None and max_score is not None) else None

    stats = {
        "corpus_size": len(corpus),
        "results_count": len(scores),
        "distinct_scores": distinct_scores,
        "min_score": min_score,
        "max_score": max_score,
        "score_span": span,
        "signal_name": signal_info["signal_name"],
        "signal_version": signal_info["version"],
        "signal_max_score": signal_info["max_score"],
    }

    if failures:
        stats["failures"] = failures
        return stats

    # Primary assertion: >= 20 distinct score values
    if distinct_scores < 20:
        failures.append(
            f"Discrimination insufficient: only {distinct_scores} distinct scores "
            f"(expected >= 20). Score span: {span}. "
            f"Scores: {sorted(set(round(s, 4) for s in scores))}"
        )
        stats["failures"] = failures

    return stats


# ---- Reporter -------------------------------------------------------------

def print_stats(stats: dict[str, Any]) -> None:
    """Pretty-print discrimination statistics."""
    print("\n" + "=" * 70)
    print(f"  Community Signal Enrichment — Quality Verification")
    print("=" * 70)
    print(f"  Signal name    : {stats['signal_name']}")
    print(f"  Version        : {stats['signal_version']}")
    print(f"  Max score      : {stats['signal_max_score']}")
    print(f"  Corpus size    : {stats['corpus_size']}")
    print(f"  Results        : {stats['results_count']}")
    print(f"  Distinct scores: {stats['distinct_scores']}  (need >= 20)")
    print(f"  Min score      : {stats['min_score']}")
    print(f"  Max score      : {stats['max_score']}")
    print(f"  Score span     : {stats['score_span']}")
    print("-" * 70)
    print("  All scores [sorted, unique]:")
    unique_sorted = sorted(set(round(s, 4) for s in stats.get("_scores", [])))
    # Reconstruct sorted unique scores from distinct_scores list
    print(f"  {unique_sorted[:10]}{' ...' if len(unique_sorted) > 10 else ''}")
    print("=" * 70)


# ---- Entry point ----------------------------------------------------------

if __name__ == "__main__":
    # Run verification
    stats = verify_enrichment_quality()

    # Re-collect scores for reporting (build from corpus so we can show sorted unique)
    corpus = _make_fingerprints()
    all_scores = [compute_score(f)[0] for f in corpus]
    stats["_scores"] = all_scores  # internal, for reporting only

    print_stats(stats)

    failures = stats.get("failures", [])
    distinct = stats["distinct_scores"]
    corpus_size = stats["corpus_size"]

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  • {f}")
        print("\n❌ QUALITY VERIFICATION FAILED")
        raise SystemExit(1)

    if distinct < 20:
        print(
            f"\n❌ Discrimination insufficient: {distinct} distinct scores "
            f"(expected >= 20 out of {corpus_size} cases)"
        )
        raise SystemExit(1)

    print(f"\n✅ Quality verification passed.")
    print(f"   {distinct} distinct scores across {corpus_size} fingerprints — "
          f"discrimination is sufficient.")
    raise SystemExit(0)