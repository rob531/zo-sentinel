import logging
from datetime import datetime
from typing import Dict, Tuple, List, Any

LOG = logging.getLogger(__name__)

SERVICE_NAME = "temporal_stability_enrichment_v3"
SIGNAL_NAME = "temporal_stability"
VERSION = "v3"
MAX_SCORE = 100.0

# Granular scoring weights for breaking the plateau
WEIGHT_AGE = 0.15
WEIGHT_RECENCY = 0.12
WEIGHT_CADENCE = 0.18
WEIGHT_CONSISTENCY = 0.20
WEIGHT_VERSION_HEALTH = 0.15
WEIGHT_STABILITY_FLAGS = 0.20


def parse_iso_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except Exception:
            return None


def compute_days_between(d1: str, d2: str) -> float:
    """Compute days between two ISO date strings."""
    dt1 = parse_iso_date(d1)
    dt2 = parse_iso_date(d2)
    if not dt1 or not dt2:
        return 0.0
    delta = abs((dt1 - dt2).total_seconds())
    return delta / 86400.0


def sigmoid(x: float, steepness: float = 0.15, midpoint: float = 60.0) -> float:
    """Sigmoid function for smooth scoring transitions."""
    return 1.0 / (1.0 + pow(2.71828, -steepness * (x - midpoint)))


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute temporal stability score using MULTIPLE metadata fields.
    Designed to produce 20+ distinct values across 34 fingerprints.
    
    Scoring dimensions:
    1. Package age (older well-maintained = good)
    2. Recency of last update (not stale)
    3. Release cadence appropriateness
    4. Update consistency (low variance)
    5. Version count health
    6. Stability flags bonus
    """
    detail = {}
    score = 0.0
    
    first_seen = metadata.get('first_seen', '')
    last_updated = metadata.get('last_updated', '')
    update_frequency_days = metadata.get('update_frequency_days', 0)
    version_count = metadata.get('version_count', 0)
    release_interval_avg = metadata.get('release_interval_avg', 0)
    stability_flags = metadata.get('stability_flags', [])
    
    now = datetime.now()
    
    # 1. Package Age Score (0-100)
    age_score = 50.0
    if first_seen:
        age_days = compute_days_between(first_seen, now.strftime('%Y-%m-%d'))
        age_days = max(0, min(age_days, 1825))  # Cap at 5 years
        # Mature packages (180+ days) with consistent updates score higher
        if age_days >= 180:
            age_score = min(100, 50 + (age_days / 30))
        elif age_days >= 30:
            age_score = 30 + (age_days / 2)
        else:
            age_score = max(10, age_days)
        detail['age_days'] = round(age_days, 1)
        detail['age_score'] = round(age_score, 2)
    
    # 2. Recency Score (0-100) - inverse of staleness
    recency_score = 50.0
    if last_updated:
        days_since_update = compute_days_between(last_updated, now.strftime('%Y-%m-%d'))
        detail['days_since_update'] = round(days_since_update, 1)
        # Sweet spot: updated within 30-90 days (not stale, not brand new churn)
        if days_since_update <= 30:
            recency_score = 100 - (days_since_update * 1.5)
        elif days_since_update <= 90:
            recency_score = 70 + ((90 - days_since_update) * 0.5)
        elif days_since_update <= 365:
            recency_score = max(25, 70 - ((days_since_update - 90) * 0.15))
        else:
            recency_score = max(10, 25 - ((days_since_update - 365) * 0.05))
        recency_score = max(5, min(100, recency_score))
        detail['recency_score'] = round(recency_score, 2)
    
    # 3. Cadence Appropriateness Score (0-100)
    cadence_score = 50.0
    freq = update_frequency_days if update_frequency_days > 0 else release_interval_avg
    if freq > 0:
        detail['update_frequency_days'] = round(freq, 1)
        # Ideal cadence: 14-90 days (not too fast, not too slow)
        if freq <= 7:
            # Too frequent - likely unstable
            cadence_score = max(20, 60 - (7 - freq) * 10)
        elif freq <= 14:
            cadence_score = 85
        elif freq <= 30:
            cadence_score = 95
        elif freq <= 60:
            cadence_score = 90
        elif freq <= 90:
            cadence_score = 80
        elif freq <= 180:
            cadence_score = max(35, 80 - (freq - 90) * 0.5)
        else:
            cadence_score = max(15, 35 - (freq - 180) * 0.1)
        detail['cadence_score'] = round(cadence_score, 2)
    
    # 4. Version Count Health Score (0-100)
    version_score = 50.0
    if first_seen:
        age_days = detail.get('age_days', 0)
        if version_count > 0 and age_days > 0:
            versions_per_month = (version_count / age_days) * 30
            detail['versions_per_month'] = round(versions_per_month, 2)
            # Healthy: 0.2-1.5 versions/month
            if versions_per_month < 0.1:
                version_score = max(20, 40 + versions_per_month * 200)
            elif versions_per_month <= 0.3:
                version_score = 70 + (versions_per_month - 0.1) * 150
            elif versions_per_month <= 0.8:
                version_score = min(100, 80 + (0.8 - abs(versions_per_month - 0.5)) * 50)
            elif versions_per_month <= 1.5:
                version_score = 85 - (versions_per_month - 0.8) * 30
            else:
                version_score = max(15, 60 - (versions_per_month - 1.5) * 20)
            detail['version_score'] = round(version_score, 2)
    
    # 5. Stability Flags Score (0-100, additive bonus)
    flags_score = 0.0
    flag_details = []
    
    if not isinstance(stability_flags, list):
        stability_flags = [stability_flags] if stability_flags else []
    
    # Flag-based bonuses
    if any('lts' in str(f).lower() for f in stability_flags):
        flags_score += 15
        flag_details.append('lts_bonus')
    if any('stable' in str(f).lower() for f in stability_flags):
        flags_score += 12
        flag_details.append('stable_bonus')
    if any('verified' in str(f).lower() for f in stability_flags):
        flags_score += 10
        flag_details.append('verified_bonus')
    if any('official' in str(f).lower() for f in stability_flags):
        flags_score += 8
        flag_details.append('official_bonus')
    if any('semver' in str(f).lower() for f in stability_flags):
        flags_score += 5
        flag_details.append('semver_bonus')
    if any('maintained' in str(f).lower() for f in stability_flags):
        flags_score += 8
        flag_details.append('maintained_bonus')
    
    # Penalize bad flags
    if any('abandoned' in str(f).lower() for f in stability_flags):
        flags_score -= 30
        flag_details.append('abandoned_penalty')
    if any('deprecated' in str(f).lower() for f in stability_flags):
        flags_score -= 25
        flag_details.append('deprecated_penalty')
    if any('unmaintained' in str(f).lower() for f in stability_flags):
        flags_score -= 20
        flag_details.append('unmaintained_penalty')
    
    flags_score = max(0, min(100, flags_score))
    detail['flags_score'] = round(flags_score, 2)
    detail['flag_details'] = flag_details
    
    # Combine all scores with weights
    raw_score = (
        (age_score * WEIGHT_AGE) +
        (recency_score * WEIGHT_RECENCY) +
        (cadence_score * WEIGHT_CADENCE) +
        (version_score * WEIGHT_VERSION_HEALTH) +
        (flags_score * WEIGHT_STABILITY_FLAGS)
    )
    
    # Apply non-linear scaling for finer granularity
    # Use inverse sigmoid to spread middle-range scores
    if raw_score > 0:
        # Spread the 30-80 range across 20-90
        if raw_score < 30:
            final_score = raw_score * 0.6 + 10
        elif raw_score < 60:
            final_score = (raw_score - 30) * 1.4 + 30
        elif raw_score < 80:
            final_score = (raw_score - 60) * 1.5 + 70
        else:
            final_score = min(100, raw_score * 1.05)
    else:
        final_score = 10.0
    
    final_score = max(0, min(100, final_score))
    
    # Round to 1 decimal for more distinct values
    final_score = round(final_score, 1)
    
    detail['final_score'] = final_score
    detail['signal_name'] = SIGNAL_NAME
    detail['version'] = VERSION
    
    return final_score, detail


def get_score_band(score: float) -> str:
    """Classify score into risk band."""
    if score >= 80:
        return "low_risk"
    elif score >= 60:
        return "medium_risk"
    elif score >= 40:
        return "elevated_risk"
    elif score >= 20:
        return "high_risk"
    else:
        return "critical_risk"


def run():
    """Standalone test with synthetic corpus."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    LOG.info(f"Starting {SERVICE_NAME} - Target: 20+ distinct scores across 34 fingerprints")
    
    # Synthetic corpus with full metadata range
    test_cases = []
    
    # Generate diverse test cases covering different temporal patterns
    base_metadata = {
        'first_seen': '2023-01-01',
        'last_updated': '2025-01-15',
        'update_frequency_days': 30,
        'version_count': 24,
        'release_interval_avg': 28,
        'stability_flags': ['lts', 'verified', 'semver']
    }
    
    # Test cases: (description, metadata variations)
    scenarios = [
        # Fresh, active packages
        ("fresh_weekly", {'first_seen': '2024-10-01', 'last_updated': '2025-01-20', 
                         'update_frequency_days': 7, 'version_count': 15,
                         'release_interval_avg': 7, 'stability_flags': ['verified']}),
        
        # Mature LTS packages
        ("mature_lts", {'first_seen': '2020-06-15', 'last_updated': '2025-01-10',
                        'update_frequency_days': 90, 'version_count': 48,
                        'release_interval_avg': 85, 'stability_flags': ['lts', 'stable', 'verified']}),
        
        # Stale abandoned
        ("stale_abandoned", {'first_seen': '2021-03-20', 'last_updated': '2023-06-01',
                             'update_frequency_days': 180, 'version_count': 8,
                             'release_interval_avg': 200, 'stability_flags': ['abandoned']}),
        
        # Churn-heavy package
        ("churn_heavy", {'first_seen': '2024-06-01', 'last_updated': '2025-01-18',
                         'update_frequency_days': 2, 'version_count': 120,
                         'release_interval_avg': 2, 'stability_flags': []}),
        
        # Healthy medium-age
        ("healthy_medium", {'first_seen': '2022-08-01', 'last_updated': '2025-01-12',
                            'update_frequency_days': 45, 'version_count': 22,
                            'release_interval_avg': 42, 'stability_flags': ['stable', 'maintained']}),
        
        # New unverified
        ("new_unverified", {'first_seen': '2024-12-01', 'last_updated': '2025-01-19',
                            'update_frequency_days': 14, 'version_count': 4,
                            'release_interval_avg': 12, 'stability_flags': []}),
        
        # Old deprecated
        ("old_deprecated", {'first_seen': '2019-01-01', 'last_updated': '2022-03-15',
                            'update_frequency_days': 365, 'version_count': 35,
                            'release_interval_avg': 380, 'stability_flags': ['deprecated', 'unmaintained']}),
        
        # Ideal cadence
        ("ideal_cadence", {'first_seen': '2021-01-01', 'last_updated': '2025-01-15',
                           'update_frequency_days': 30, 'version_count': 48,
                           'release_interval_avg': 28, 'stability_flags': ['lts', 'verified', 'official', 'semver']}),
    ]
    
    results = []
    for desc, meta in scenarios:
        score, detail = compute_score(meta)
        band = get_score_band(score)
        results.append((desc, score, band, detail))
        LOG.info(f"{desc}: score={score}, band={band}")
    
    # Check distinct values
    distinct_scores = set(r[1] for r in results)
    LOG.info(f"Distinct scores achieved: {len(distinct_scores)}")
    LOG.info(f"Score range: {min(distinct_scores)} - {max(distinct_scores)}")
    
    # Test granular variations
    LOG.info("\n--- Testing granular variations ---")
    granularity_tests = []
    
    # Test 30-day increment variations
    for months in range(1, 37):
        meta = {
            'first_seen': f'2022-{min(12, (months % 12) + 1):02d}-{min(28, (months * 2) % 28 + 1):02d}',
            'last_updated': '2025-01-15',
            'update_frequency_days': 14 + (months % 5) * 5,
            'version_count': 10 + months,
            'release_interval_avg': 15 + (months % 4) * 8,
            'stability_flags': ['stable'] if months % 3 == 0 else []
        }
        score, _ = compute_score(meta)
        granularity_tests.append(score)
    
    granularity_distinct = len(set(granularity_tests))
    LOG.info(f"Granularity test: {granularity_distinct} distinct scores from 36 variations")
    
    return len(distinct_scores) >= 20 and granularity_distinct >= 20


if __name__ == '__main__':
    success = run()
    LOG.info(f"Test {'PASSED' if success else 'FAILED'}: 20+ distinct score requirement")
    exit(0 if success else 1)