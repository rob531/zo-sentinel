SIGNAL_NAME = "vendor_concentration"
VERSION = "v1"
MAX_SCORE = 100

DISPOSABLE_VENDOR_PATTERNS = [
    "<email>@gmail.com",
    "<email>@yahoo.com",
    "<email>@hotmail.com",
    "test",
    "tmp",
    "demo",
    "example",
    "temp",
    "fake",
    "throwaway",
]

def compute_score(metadata: dict) -> tuple[float, dict]:
    evidence = {}
    score_delta = 0.0

    maintainer_count = metadata.get("maintainer_count", 0)
    if maintainer_count == 1:
        penalty = -20.0
        score_delta += penalty
        evidence["maintainer_single_penalty"] = {
            "magnitude": penalty,
            "reason": "Single maintainer: single point of failure risk"
        }

    author = metadata.get("author", "")
    if author:
        author_lower = author.lower()
        for pattern in DISPOSABLE_VENDOR_PATTERNS:
            if pattern in author_lower:
                penalty = -15.0
                score_delta += penalty
                evidence["disposable_author_penalty"] = {
                    "magnitude": penalty,
                    "matched_pattern": pattern,
                    "reason": "Author matches disposable vendor watchlist pattern"
                }
                break

    observed_in_registries = metadata.get("observed_in_registries", 0)
    if observed_in_registries is not None and observed_in_registries >= 3:
        bonus = 15.0
        score_delta += bonus
        evidence["registry_diversity_bonus"] = {
            "magnitude": bonus,
            "observed_count": observed_in_registries,
            "reason": "Package corroborated in >= 3 registries"
        }

    return score_delta, evidence

def get_score_band(score_delta: float) -> str:
    if score_delta >= 15:
        return "LOW_RISK"
    elif score_delta >= 0:
        return "MEDIUM_RISK"
    elif score_delta >= -20:
        return "HIGH_RISK"
    else:
        return "CRITICAL"

def run():
    test_metadata = {
        "author": "user@gmail.com",
        "maintainer_count": 1,
        "registry_source": "npm",
        "observed_in_registries": 4
    }
    score_delta, evidence = compute_score(test_metadata)
    print(f"Signal: {SIGNAL_NAME} {VERSION}")
    print(f"Metadata: {test_metadata}")
    print(f"Score delta: {score_delta}")
    print(f"Band: {get_score_band(score_delta)}")
    print(f"Evidence: {evidence}")

if __name__ == "__main__":
    run()