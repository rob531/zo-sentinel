import math
from typing import Dict, Tuple, Any


MAX_STARS_SCORE = 15.0
MAX_DOWNLOAD_SCORE = 20.0
MAX_AGE_SCORE = 10.0
MAX_PUBLISHER_VERIFIED_SCORE = 10.0
MAX_DEPENDENCY_PENALTY = 15.0
DEPENDENCY_PENALTY_PER_DEP = 0.5

REGISTRY_MULTIPLIERS = {
    'github': 1.2,
    'pypi': 1.1,
    'npm': 1.0,
    'other': 0.8
}

STARS_THRESHOLD = 10000
DOWNLOAD_THRESHOLD = 1000000
AGE_THRESHOLD = 3650
DEPENDENCY_PENALTY_THRESHOLD = 30


def sigmoid(x: float, center: float = 0.5) -> float:
    """Sigmoid curve for smooth score ramping."""
    x_adj = (x - center) * 10
    return 1.0 / (1.0 + math.exp(-x_adj))


def softmax_weight(values: list, index: int) -> float:
    """Compute softmax weight for a single value."""
    max_val = max(values) if values else 0
    exp_val = math.exp(values[index] - max_val)
    total = sum(math.exp(v - max_val) for v in values)
    return exp_val / total if total > 0 else 0.0


def log_normalize(value: float, threshold: float) -> float:
    """Logarithmic normalization for heavily-skewed distributions."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(threshold))


def hash_string(s: str) -> float:
    """Deterministic hash for any string value."""
    h = 0
    for i, c in enumerate(s):
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return (h & 0xFFFFFFFF) / 0xFFFFFFFF


def score_stars(stars: Any) -> Tuple[float, float]:
    """Score based on GitHub stars. Returns (score, raw_stars)."""
    raw = float(stars) if stars is not None else 0.0
    if raw <= 0:
        return 0.0, 0.0
    normalized = log_normalize(raw, STARS_THRESHOLD)
    score = normalized * MAX_STARS_SCORE
    return score, raw


def score_download_count(download_count: Any) -> Tuple[float, float]:
    """Score based on package download count. Returns (score, raw_count)."""
    raw = float(download_count) if download_count is not None else 0.0
    if raw <= 0:
        return 0.0, 0.0
    normalized = log_normalize(raw, DOWNLOAD_THRESHOLD)
    score = normalized * MAX_DOWNLOAD_SCORE
    return score, raw


def score_age_days(age_days: Any) -> Tuple[float, float]:
    """Score based on project age. Returns (score, raw_age)."""
    raw = float(age_days) if age_days is not None else 0.0
    if raw <= 0:
        return 0.0, 0.0
    normalized = min(1.0, raw / AGE_THRESHOLD)
    score = normalized * MAX_AGE_SCORE
    return score, raw


def score_registry_source(registry_source: Any) -> float:
    """Get quality multiplier for registry source."""
    if registry_source is None:
        return REGISTRY_MULTIPLIERS['other']
    source_str = str(registry_source).lower().strip()
    return REGISTRY_MULTIPLIERS.get(source_str, REGISTRY_MULTIPLIERS['other'])


def score_publisher_verified(publisher_verified: Any) -> Tuple[float, bool]:
    """Score for verified publisher status. Returns (score, verified_bool)."""
    verified = bool(publisher_verified) if publisher_verified is not None else False
    score = MAX_PUBLISHER_VERIFIED_SCORE if verified else 0.0
    return score, verified


def score_dependency_count(dependency_count: Any) -> Tuple[float, float]:
    """Score penalty for dependency count. Returns (penalty, raw_count)."""
    raw = float(dependency_count) if dependency_count is not None else 0.0
    penalty = min(MAX_DEPENDENCY_PENALTY, raw * DEPENDENCY_PENALTY_PER_DEP)
    return penalty, raw


def compute_score(metadata: dict) -> Tuple[float, dict]:
    """
    Compute community signal score from metadata.
    
    Args:
        metadata: dict with optional keys:
            - stars: int/float - GitHub star count
            - download_count: int/float - package download count  
            - age_days: int/float - days since creation
            - registry_source: str - npm, github, pypi, other
            - publisher_verified: bool - verified publisher flag
            - dependency_count: int/float - number of dependencies
    
    Returns:
        tuple: (score clamped to [0,100], evidence dict)
    """
    stars = metadata.get('stars')
    download_count = metadata.get('download_count')
    age_days = metadata.get('age_days')
    registry_source = metadata.get('registry_source')
    publisher_verified = metadata.get('publisher_verified')
    dependency_count = metadata.get('dependency_count')
    
    stars_score, raw_stars = score_stars(stars)
    download_score, raw_downloads = score_download_count(download_count)
    age_score, raw_age = score_age_days(age_days)
    registry_multiplier = score_registry_source(registry_source)
    verified_score, is_verified = score_publisher_verified(publisher_verified)
    dep_penalty, raw_deps = score_dependency_count(dependency_count)
    
    raw_score = stars_score + download_score + age_score + verified_score - dep_penalty
    
    multiplier_applied = raw_score * (registry_multiplier - 1.0)
    final_score = raw_score * registry_multiplier
    
    clamped_score = max(0.0, min(100.0, final_score))
    
    fields_available = sum(1 for v in [stars, download_count, age_days, publisher_verified, dependency_count] if v is not None)
    confidence_band = 'high' if fields_available >= 5 else 'medium' if fields_available >= 3 else 'low'
    
    evidence = {
        'stars': raw_stars,
        'stars_score': round(stars_score, 4),
        'download_count': raw_downloads,
        'download_score': round(download_score, 4),
        'age_days': raw_age,
        'age_score': round(age_score, 4),
        'registry_source': str(registry_source).lower() if registry_source else 'other',
        'registry_multiplier': registry_multiplier,
        'publisher_verified': is_verified,
        'verified_score': round(verified_score, 4),
        'dependency_count': raw_deps,
        'dependency_penalty': round(dep_penalty, 4),
        'raw_score': round(raw_score, 4),
        'final_score': round(clamped_score, 4),
        'confidence_band': confidence_band,
        'signal_type': 'community',
        'version': '2.0'
    }
    
    return round(clamped_score, 2), evidence


def compute_batch_scores(metadatas: list) -> list:
    """Compute scores for a batch of metadata dicts."""
    results = []
    for metadata in metadatas:
        score, evidence = compute_score(metadata)
        results.append({'score': score, 'evidence': evidence})
    return results


def get_score_band(score: float) -> str:
    """Categorize score into risk band."""
    if score >= 80:
        return 'trusted'
    elif score >= 60:
        return 'research'
    elif score >= 40:
        return 'caution'
    elif score >= 20:
        return 'limited'
    else:
        return 'unknown'


def run():
    """Standalone test runner for enrichment_harness.py contract."""
    from datetime import datetime, timezone
    
    test_cases = []
    
    base_age = 365
    for i in range(34):
        stars = (i % 10) * 500
        downloads = (i % 10) * 50000
        age_days = base_age + (i % 10) * 100
        sources = ['github', 'npm', 'pypi', 'other']
        registry_source = sources[i % 4]
        verified = i % 3 == 0
        deps = i * 2
        
        metadata = {
            'stars': stars,
            'download_count': downloads,
            'age_days': age_days,
            'registry_source': registry_source,
            'publisher_verified': verified,
            'dependency_count': deps
        }
        test_cases.append(metadata)
    
    results = []
    for tc in test_cases:
        score, evidence = compute_score(tc)
        results.append((score, evidence))
    
    distinct_scores = len(set(r[0] for r in results))
    
    print(f"Community Signal Enrichment v2 Test Results")
    print(f"=" * 50)
    print(f"Total test cases: {len(results)}")
    print(f"Distinct scores achieved: {distinct_scores}")
    print(f"Min score: {min(r[0] for r in results):.2f}")
    print(f"Max score: {max(r[0] for r in results):.2f}")
    print(f"Mean score: {sum(r[0] for r in results) / len(results):.2f}")
    print()
    
    for idx, (score, evidence) in enumerate(results[:5]):
        print(f"Case {idx}: score={score:.2f}, band={get_score_band(score)}, source={evidence['registry_source']}, multiplier={evidence['registry_multiplier']}")
    
    print()
    print(f"Score distribution by band:")
    bands = {'trusted': 0, 'research': 0, 'caution': 0, 'limited': 0, 'unknown': 0}
    for score, _ in results:
        bands[get_score_band(score)] += 1
    for band, count in bands.items():
        print(f"  {band}: {count}")


if __name__ == '__main__':
    run()