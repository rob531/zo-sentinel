import hashlib
import math
import os
from datetime import datetime

# =============================================================================
# INJECTION_RESILIENCE_ENRICHMENT
# Pure computation module for signal_analyser composite scoring
# Phase 8: injection_resilience dimension enrichment from registry metadata
# =============================================================================

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'injection_resilience_enrichment.log')

SERVICE_NAME = 'injection_resilience_enrichment'
SIGNAL_NAME = 'injection_resilience'
VERSION = 'v1.0.0'
MAX_SCORE = 100.0

# Registry trust weights (higher = more vetted/organic growth)
REGISTRY_TRUST = {
    'npm': 0.90,
    'pypi': 0.85,
    'github': 0.75,
    'smithery': 0.60,
    'npm_knowledge_base': 0.70,
    'manual': 0.50,
    'unknown': 0.30
}

# Weight configuration for composite score
WEIGHTS = {
    'registry_source': 0.30,
    'age_days': 0.25,
    'download_count': 0.20,
    'stars': 0.15,
    'publisher_verified': 0.10
}

def sigmoid(x: float, center: float = 0.5, steepness: float = 5.0) -> float:
    """Sigmoid normalization for bounded 0-1 output."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - center)))

def softmax_weight(values: list[float]) -> list[float]:
    """Softmax normalization for weighted contributions."""
    if not values:
        return []
    max_val = max(values)
    exp_vals = [math.exp(v - max_val) for v in values]
    sum_exp = sum(exp_vals)
    if sum_exp == 0:
        return [0.0] * len(values)
    return [e / sum_exp for e in exp_vals]

def log_normalize(value: float, scale: float = 1000.0) -> float:
    """Log normalization for skewed distributions."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(scale))

def hash_string(s: str) -> float:
    """Deterministic hash for consistent scoring."""
    h = hashlib.sha256(s.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFFFFFFFFFF

def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.utcnow().isoformat() + 'Z'

def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute injection_resilience score from registry metadata.
    
    Args:
        metadata: dict with optional keys:
            - registry_source: str (npm, pypi, github, smithery, etc.)
            - age_days: int (days since first publication)
            - download_count: int (total downloads)
            - stars: int (GitHub stars or equivalent)
            - publisher_verified: bool (has verified publisher identity)
    
    Returns:
        tuple[float, dict]: (score 0-100, evidence blob)
    
    Signal invariant: returns score in range [0, 100] and evidence dict
    with signal_type and confidence fields.
    """
    # Extract metadata fields with defaults
    registry_source = metadata.get('registry_source', 'unknown').lower()
    age_days = int(metadata.get('age_days', 0))
    download_count = int(metadata.get('download_count', 0))
    stars = int(metadata.get('stars', 0))
    publisher_verified = bool(metadata.get('publisher_verified', False))
    
    # Compute individual component scores
    registry_score = _score_registry_source(registry_source)
    age_score = _score_age_days(age_days)
    download_score = _score_download_count(download_count)
    stars_score = _score_stars(stars)
    verified_score = _score_publisher_verified(publisher_verified)
    
    # Weighted composite score
    raw_score = (
        registry_score * WEIGHTS['registry_source'] +
        age_score * WEIGHTS['age_days'] +
        download_score * WEIGHTS['download_count'] +
        stars_score * WEIGHTS['stars'] +
        verified_score * WEIGHTS['publisher_verified']
    )
    
    # Scale to 0-100
    score = min(100.0, max(0.0, raw_score * 100.0))
    
    # Compute confidence based on data completeness
    confidence = _compute_confidence(metadata, registry_score, age_score, download_score, stars_score, verified_score)
    
    # Build evidence blob
    evidence = {
        'signal_type': SIGNAL_NAME,
        'version': VERSION,
        'computed_at': utc_now_iso(),
        'confidence': confidence,
        'score': score,
        'components': {
            'registry_source': {
                'value': registry_source,
                'score': registry_score,
                'weight': WEIGHTS['registry_source']
            },
            'age_days': {
                'value': age_days,
                'score': age_score,
                'weight': WEIGHTS['age_days']
            },
            'download_count': {
                'value': download_count,
                'score': download_score,
                'weight': WEIGHTS['download_count']
            },
            'stars': {
                'value': stars,
                'score': stars_score,
                'weight': WEIGHTS['stars']
            },
            'publisher_verified': {
                'value': publisher_verified,
                'score': verified_score,
                'weight': WEIGHTS['publisher_verified']
            }
        },
        'raw_composite': raw_score
    }
    
    return score, evidence

def _score_registry_source(registry_source: str) -> float:
    """Score based on registry trust level."""
    return REGISTRY_TRUST.get(registry_source, REGISTRY_TRUST['unknown'])

def _score_age_days(age_days: int) -> float:
    """
    Score based on package age.
    Older packages have more track record and community scrutiny.
    Log scale with diminishing returns after ~365 days.
    """
    if age_days <= 0:
        return 0.0
    # Sigmoid with inflection at ~90 days, asymptote at 365+
    base_score = sigmoid(age_days / 365.0, center=0.25, steepness=4.0)
    return base_score

def _score_download_count(download_count: int) -> float:
    """
    Score based on download count.
    High download counts indicate widespread usage and testing.
    """
    if download_count <= 0:
        return 0.0
    # Log normalize with scale 1M downloads
    return log_normalize(download_count, scale=1_000_000)

def _score_stars(stars: int) -> float:
    """
    Score based on GitHub stars or equivalent.
    Stars indicate community engagement and scrutiny.
    """
    if stars <= 0:
        return 0.0
    # Log normalize with scale 10K stars
    return log_normalize(stars, scale=10_000)

def _score_publisher_verified(publisher_verified: bool) -> float:
    """Score based on publisher identity verification."""
    return 1.0 if publisher_verified else 0.0

def _compute_confidence(
    metadata: dict,
    registry_score: float,
    age_score: float,
    download_score: float,
    stars_score: float,
    verified_score: float
) -> float:
    """
    Compute confidence score (0-1) based on data completeness and signals.
    """
    # Count non-zero component scores
    non_zero_components = sum([
        1 if registry_score > 0 else 0,
        1 if age_score > 0 else 0,
        1 if download_score > 0 else 0,
        1 if stars_score > 0 else 0,
        1 if verified_score > 0 else 0
    ])
    
    # Base confidence from data completeness (0.0 to 1.0)
    base_confidence = non_zero_components / 5.0
    
    # Boost for high-value signals
    boost = 0.0
    if registry_score >= 0.8:
        boost += 0.1
    if age_score >= 0.7:
        boost += 0.1
    if download_score >= 0.5:
        boost += 0.1
    if stars_score >= 0.5:
        boost += 0.05
    if verified_score >= 0.8:
        boost += 0.15
    
    # Combine with diminishing returns (cap at 1.0)
    confidence = min(1.0, base_confidence + boost * base_confidence)
    
    return round(confidence, 3)

def get_score_band(score: float) -> str:
    """Return risk band for score."""
    if score >= 80:
        return 'LOW_RISK'
    elif score >= 60:
        return 'MEDIUM_RISK'
    elif score >= 40:
        return 'ELEVATED_RISK'
    else:
        return 'HIGH_RISK'

def run() -> None:
    """Standalone test run."""
    # Ensure log directory exists
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # Test cases with varied metadata
    test_cases = [
        {
            'name': 'Mature npm package with verification',
            'metadata': {
                'registry_source': 'npm',
                'age_days': 730,
                'download_count': 5_000_000,
                'stars': 2000,
                'publisher_verified': True
            }
        },
        {
            'name': 'New GitHub package',
            'metadata': {
                'registry_source': 'github',
                'age_days': 30,
                'download_count': 500,
                'stars': 50,
                'publisher_verified': False
            }
        },
        {
            'name': 'Unknown registry, minimal data',
            'metadata': {
                'registry_source': 'unknown',
                'age_days': 0,
                'download_count': 0,
                'stars': 0,
                'publisher_verified': False
            }
        },
        {
            'name': 'PyPI package, moderate age',
            'metadata': {
                'registry_source': 'pypi',
                'age_days': 180,
                'download_count': 100_000,
                'stars': 500,
                'publisher_verified': True
            }
        }
    ]
    
    print(f"=== {SERVICE_NAME} {VERSION} Test ===")
    print()
    
    for tc in test_cases:
        score, evidence = compute_score(tc['metadata'])
        band = get_score_band(score)
        
        print(f"Test: {tc['name']}")
        print(f"  Metadata: {tc['metadata']}")
        print(f"  Score: {score:.1f} ({band})")
        print(f"  Confidence: {evidence['confidence']:.3f}")
        print(f"  Components:")
        for comp_name, comp_data in evidence['components'].items():
            print(f"    {comp_name}: {comp_data['value']} -> score={comp_data['score']:.3f}")
        print()

if __name__ == '__main__':
    run()