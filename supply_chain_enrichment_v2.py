import logging
import hashlib
from typing import Any, Dict, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/supply_chain_enrichment_v2.log'
)
LOG = logging.getLogger(__name__)

SERVICE_NAME = 'supply_chain_enrichment_v2'
SIGNAL_NAME = 'supply_chain'
VERSION = 2
MAX_SCORE = 1.0
MIN_SCORE = 0.0

WEIGHTS = {
    'registry_source': 0.20,
    'age_days': 0.15,
    'dependency_count': 0.15,
    'publisher_verified': 0.20,
    'stars': 0.10,
    'has_licenses': 0.10,
    'vulnerability_count': 0.10,
}


def sigmoid(x: float, center: float = 0.0, steepness: float = 0.05) -> float:
    val = 1.0 / (1.0 + __import__('math').exp(-steepness * (x - center)))
    return float(val)


def softmax_weight(values: Dict[str, float]) -> Dict[str, float]:
    exp_vals = {k: __import__('math').exp(v) for k, v in values.items()}
    total = sum(exp_vals.values())
    return {k: v / total for k, v in exp_vals.items()}


def log_normalize(value: float, base: float = 10.0) -> float:
    if value <= 0:
        return 0.0
    import math
    return math.log1p(value) / math.log(base)


def hash_string(s: str) -> int:
    return int(hashlib.md5(s.encode('utf-8')).hexdigest()[:8], 16)


def score_registry_source(registry_source: str | None) -> float:
    if not registry_source:
        return 0.1
    src = registry_source.lower().strip()
    TRUSTED_SOURCES = {
        'npm': 0.95,
        'github': 0.90,
        'smithery': 0.80,
        'arcade': 0.85,
        'pypi': 0.90,
        'cargo': 0.88,
        'nuget': 0.85,
        'maven': 0.87,
        'cocoapods': 0.83,
        'pub': 0.82,
    }
    for known, score in TRUSTED_SOURCES.items():
        if known in src:
            return score
    if 'official' in src or 'verified' in src:
        return 0.75
    return 0.40


def score_age_days(age_days: int | float | None) -> float:
    if age_days is None:
        return 0.3
    try:
        age = float(age_days)
    except (TypeError, ValueError):
        return 0.3
    if age < 0:
        return 0.1
    if age < 7:
        return 0.20
    elif age < 30:
        return 0.40
    elif age < 90:
        return 0.60
    elif age < 180:
        return 0.75
    elif age < 365:
        return 0.85
    elif age < 730:
        return 0.92
    else:
        return 1.0


def score_dependency_count(dep_count: int | float | None) -> float:
    if dep_count is None:
        return 0.50
    try:
        deps = float(dep_count)
    except (TypeError, ValueError):
        return 0.50
    if deps < 0:
        return 0.20
    if deps == 0:
        return 0.60
    if deps <= 3:
        return 0.75
    elif deps <= 10:
        return 0.85
    elif deps <= 50:
        return 0.92
    else:
        norm = sigmoid(deps, center=100.0, steepness=0.02)
        return 0.70 + (norm * 0.25)


def score_publisher_verified(publisher_verified: bool | int | str | None) -> float:
    if publisher_verified is None:
        return 0.35
    if isinstance(publisher_verified, bool):
        return 1.0 if publisher_verified else 0.20
    if isinstance(publisher_verified, int):
        return 1.0 if publisher_verified else 0.20
    if isinstance(publisher_verified, str):
        val = str(publisher_verified).lower().strip()
        truthy = {'true', '1', 'yes', 'verified', 'confirmed', 'authored'}
        return 1.0 if val in truthy else 0.20
    return 0.35


def score_stars(stars: int | float | None) -> float:
    if stars is None:
        return 0.30
    try:
        s = float(stars)
    except (TypeError, ValueError):
        return 0.30
    if s < 0:
        return 0.10
    if s == 0:
        return 0.25
    norm = log_normalize(s, base=10.0)
    return min(1.0, 0.30 + norm * 0.65)


def score_has_licenses(has_licenses: bool | int | str | list | None) -> float:
    if has_licenses is None:
        return 0.40
    if isinstance(has_licenses, bool):
        return 1.0 if has_licenses else 0.25
    if isinstance(has_licenses, int):
        return 1.0 if has_licenses else 0.25
    if isinstance(has_licenses, str):
        val = str(has_licenses).lower().strip()
        if val in ('true', '1', 'yes', 'licensed', 'mit', 'apache', 'bsd', 'gpl'):
            return 0.90
        if val in ('false', '0', 'no', 'none', ''):
            return 0.25
        return 0.60
    if isinstance(has_licenses, list):
        return 1.0 if len(has_licenses) > 0 else 0.25
    return 0.40


def score_vulnerability_count(vuln_count: int | float | None) -> float:
    if vuln_count is None:
        return 0.60
    try:
        v = float(vuln_count)
    except (TypeError, ValueError):
        return 0.60
    if v < 0:
        return 0.60
    if v == 0:
        return 1.0
    elif v == 1:
        return 0.70
    elif v == 2:
        return 0.50
    elif v <= 5:
        return 0.30
    else:
        return 0.10


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    sub_scores: Dict[str, float] = {}

    sub_scores['registry_source'] = score_registry_source(metadata.get('registry_source'))
    sub_scores['age_days'] = score_age_days(metadata.get('age_days'))
    sub_scores['dependency_count'] = score_dependency_count(metadata.get('dependency_count'))
    sub_scores['publisher_verified'] = score_publisher_verified(metadata.get('publisher_verified'))
    sub_scores['stars'] = score_stars(metadata.get('stars'))
    sub_scores['has_licenses'] = score_has_licenses(metadata.get('has_licenses'))
    sub_scores['vulnerability_count'] = score_vulnerability_count(metadata.get('vulnerability_count'))

    total = 0.0
    for key, weight in WEIGHTS.items():
        total += sub_scores.get(key, 0.0) * weight

    score = max(MIN_SCORE, min(MAX_SCORE, round(total, 6)))

    evidence = {
        'registry_source': metadata.get('registry_source'),
        'registry_source_score': sub_scores['registry_source'],
        'age_days': metadata.get('age_days'),
        'age_days_score': sub_scores['age_days'],
        'dependency_count': metadata.get('dependency_count'),
        'dependency_count_score': sub_scores['dependency_count'],
        'publisher_verified': metadata.get('publisher_verified'),
        'publisher_verified_score': sub_scores['publisher_verified'],
        'stars': metadata.get('stars'),
        'stars_score': sub_scores['stars'],
        'has_licenses': metadata.get('has_licenses'),
        'has_licenses_score': sub_scores['has_licenses'],
        'vulnerability_count': metadata.get('vulnerability_count'),
        'vulnerability_count_score': sub_scores['vulnerability_count'],
        'weighted_total': round(total, 6),
        'signal': SIGNAL_NAME,
        'version': VERSION,
    }

    return score, evidence


def compute_batch_scores(rows: list[Dict[str, Any]]) -> list[Tuple[float, Dict[str, Any]]]:
    return [compute_score(row) for row in rows]


def get_score_band(score: float) -> str:
    if score >= 0.85:
        return 'excellent'
    elif score >= 0.70:
        return 'good'
    elif score >= 0.50:
        return 'moderate'
    elif score >= 0.30:
        return 'poor'
    else:
        return 'critical'


def run() -> None:
    LOG.info(f"{SERVICE_NAME} v{VERSION} — supply chain enrichment with multi-field discrimination")
    LOG.info(f"Weights: {WEIGHTS}")
    test_metadata = {
        'registry_source': 'npm',
        'age_days': 365,
        'dependency_count': 15,
        'publisher_verified': True,
        'stars': 500,
        'has_licenses': True,
        'vulnerability_count': 0,
    }
    score, evidence = compute_score(test_metadata)
    LOG.info(f"Test score: {score} | band: {get_score_band(score)}")
    LOG.info(f"Evidence: {evidence}")


if __name__ == '__main__':
    run()