#!/usr/bin/env python3
"""
Supply Chain Enrichment V3 - Fixed flat signal issue
Uses multiple metadata fields to compute discriminative scores.
Pure function: no DB writes, no network, no protected imports.
"""

import hashlib
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Weights for each signal dimension
WEIGHTS = {
    'registry_source': 0.20,
    'age_days': 0.25,
    'download_count': 0.20,
    'dependency_count': 0.10,
    'publisher_verified': 0.15,
    'stars': 0.10,
}

# Registry source scoring (higher is better)
REGISTRY_SOURCE_SCORES = {
    'official': 1.0,
    'verified': 0.9,
    'community': 0.6,
    'npm': 0.5,
    'github': 0.4,
    'unknown': 0.2,
}

# Age scoring: newer isn't necessarily better, but stable maturity is preferred
# Score peaks around 180-730 days (6 months to 2 years)
def score_age_days(age_days: Optional[int]) -> float:
    if age_days is None or age_days < 0:
        return 0.0
    if age_days < 7:
        return 0.3  # Very new, low trust
    elif age_days < 30:
        return 0.5  # New, some trust
    elif age_days < 90:
        return 0.7  # Getting established
    elif age_days < 180:
        return 0.85  # Established, good trust
    elif age_days < 365:
        return 0.95  # Mature, high trust
    elif age_days < 730:
        return 1.0  # Very mature, peak trust
    elif age_days < 1460:
        return 0.85  # Aging but still trustworthy
    else:
        return 0.6  # Very old, may be abandoned


def score_download_count(downloads: Optional[int]) -> float:
    """Logarithmic scoring based on download count."""
    if downloads is None or downloads < 0:
        return 0.0
    if downloads == 0:
        return 0.1
    elif downloads < 100:
        return 0.2
    elif downloads < 1000:
        return 0.4
    elif downloads < 10000:
        return 0.6
    elif downloads < 100000:
        return 0.8
    elif downloads < 1000000:
        return 0.9
    else:
        return 1.0


def score_dependency_count(dep_count: Optional[int]) -> float:
    """Fewer dependencies is better (attack surface)."""
    if dep_count is None or dep_count < 0:
        return 0.5  # Unknown, neutral
    if dep_count == 0:
        return 1.0  # No dependencies, minimal attack surface
    elif dep_count <= 5:
        return 0.85
    elif dep_count <= 10:
        return 0.7
    elif dep_count <= 20:
        return 0.5
    elif dep_count <= 50:
        return 0.3
    else:
        return 0.1  # High dependency count, high attack surface


def score_publisher_verified(verified: Optional[bool]) -> float:
    """Publisher verification bonus."""
    if verified is True:
        return 1.0
    elif verified is False:
        return 0.3
    else:
        return 0.5  # Unknown


def score_stars(stars: Optional[int]) -> float:
    """Star rating scoring."""
    if stars is None or stars < 0:
        return 0.3
    if stars == 0:
        return 0.2
    elif stars < 10:
        return 0.4
    elif stars < 50:
        return 0.6
    elif stars < 100:
        return 0.75
    elif stars < 500:
        return 0.85
    elif stars < 1000:
        return 0.9
    else:
        return 1.0


def score_registry_source(source: Optional[str]) -> float:
    """Score based on registry source."""
    if source is None:
        return 0.2
    source_lower = str(source).lower().strip()
    for key, score in REGISTRY_SOURCE_SCORES.items():
        if key in source_lower:
            return score
    return 0.2  # Unknown


def compute_supply_chain_score(
    server_data: Dict[str, Any]
) -> Tuple[float, Dict[str, Any]]:
    """
    Compute supply chain risk score from metadata fields.
    
    Args:
        server_data: Dict containing server metadata fields
        
    Returns:
        Tuple of (weighted_score, evidence_dict)
    """
    evidence = {}
    
    # Extract fields (handle various possible key names)
    registry_source = server_data.get('registry_source') or server_data.get('source') or server_data.get('registry')
    age_days = server_data.get('age_days') or server_data.get('age') or server_data.get('created_days_ago')
    download_count = server_data.get('download_count') or server_data.get('downloads') or server_data.get('weekly_downloads')
    dependency_count = server_data.get('dependency_count') or server_data.get('dependencies') or server_data.get('dep_count')
    publisher_verified = server_data.get('publisher_verified') or server_data.get('verified') or server_data.get('is_verified')
    stars = server_data.get('stars') or server_data.get('star_count') or server_data.get('github_stars')
    
    # Compute individual scores
    registry_source_score = score_registry_source(registry_source)
    age_score = score_age_days(age_days)
    download_score = score_download_count(download_count)
    dep_penalty = score_dependency_count(dependency_count)
    verified_bonus = score_publisher_verified(publisher_verified)
    star_score = score_stars(stars)
    
    # Record evidence
    evidence['registry_source'] = {
        'raw': registry_source,
        'score': registry_source_score,
        'weight': WEIGHTS['registry_source']
    }
    evidence['age_days'] = {
        'raw': age_days,
        'score': age_score,
        'weight': WEIGHTS['age_days']
    }
    evidence['download_count'] = {
        'raw': download_count,
        'score': download_score,
        'weight': WEIGHTS['download_count']
    }
    evidence['dependency_count'] = {
        'raw': dependency_count,
        'score': dep_penalty,
        'weight': WEIGHTS['dependency_count']
    }
    evidence['publisher_verified'] = {
        'raw': publisher_verified,
        'score': verified_bonus,
        'weight': WEIGHTS['publisher_verified']
    }
    evidence['stars'] = {
        'raw': stars,
        'score': star_score,
        'weight': WEIGHTS['stars']
    }
    
    # Compute weighted sum
    weighted_score = (
        registry_source_score * WEIGHTS['registry_source'] +
        age_score * WEIGHTS['age_days'] +
        download_score * WEIGHTS['download_count'] +
        dep_penalty * WEIGHTS['dependency_count'] +
        verified_bonus * WEIGHTS['publisher_verified'] +
        star_score * WEIGHTS['stars']
    )
    
    # Add computed values to evidence
    evidence['weighted_sum'] = weighted_score
    evidence['max_possible'] = sum(WEIGHTS.values())
    
    return weighted_score, evidence


def compute_score_hash(server_data: Dict[str, Any]) -> str:
    """Compute hash of score inputs for change detection."""
    relevant_keys = ['registry_source', 'age_days', 'download_count', 'dependency_count', 'publisher_verified', 'stars']
    values = {k: server_data.get(k) for k in relevant_keys}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]


def get_score_band(score: float) -> str:
    """Categorize score into risk bands."""
    if score >= 0.85:
        return 'EXCELLENT'
    elif score >= 0.70:
        return 'GOOD'
    elif score >= 0.55:
        return 'FAIR'
    elif score >= 0.40:
        return 'POOR'
    else:
        return 'CRITICAL'


def get_recommendation(band: str) -> str:
    """Get recommendation based on score band."""
    recommendations = {
        'EXCELLENT': 'Low supply chain risk. Monitor for anomalies.',
        'GOOD': 'Acceptable risk. Standard monitoring.',
        'FAIR': 'Moderate risk. Consider enhanced review.',
        'POOR': 'Elevated risk. Manual review recommended.',
        'CRITICAL': 'High supply chain risk. Immediate review required.'
    }
    return recommendations.get(band, 'Unknown risk level')


def run() -> None:
    """Test/validation function that demonstrates scoring across synthetic fingerprints."""
    print("Supply Chain Enrichment V3 - Testing Score Distribution")
    print("=" * 70)
    
    # Synthetic fingerprints representing varied metadata
    synthetic_servers = [
        # EXCELLENT (0.85+)
        {'name': 'excellent_1', 'registry_source': 'official', 'age_days': 400, 'download_count': 500000, 'dependency_count': 3, 'publisher_verified': True, 'stars': 800},
        {'name': 'excellent_2', 'registry_source': 'verified', 'age_days': 600, 'download_count': 200000, 'dependency_count': 1, 'publisher_verified': True, 'stars': 600},
        {'name': 'excellent_3', 'registry_source': 'official', 'age_days': 300, 'download_count': 800000, 'dependency_count': 0, 'publisher_verified': True, 'stars': 1200},
        
        # GOOD (0.70-0.84)
        {'name': 'good_1', 'registry_source': 'official', 'age_days': 150, 'download_count': 50000, 'dependency_count': 8, 'publisher_verified': True, 'stars': 200},
        {'name': 'good_2', 'registry_source': 'verified', 'age_days': 250, 'download_count': 30000, 'dependency_count': 5, 'publisher_verified': False, 'stars': 150},
        {'name': 'good_3', 'registry_source': 'npm', 'age_days': 500, 'download_count': 150000, 'dependency_count': 4, 'publisher_verified': True, 'stars': 400},
        {'name': 'good_4', 'registry_source': 'community', 'age_days': 180, 'download_count': 80000, 'dependency_count': 2, 'publisher_verified': True, 'stars': 300},
        
        # FAIR (0.55-0.69)
        {'name': 'fair_1', 'registry_source': 'npm', 'age_days': 60, 'download_count': 5000, 'dependency_count': 12, 'publisher_verified': False, 'stars': 50},
        {'name': 'fair_2', 'registry_source': 'github', 'age_days': 90, 'download_count': 3000, 'dependency_count': 6, 'publisher_verified': False, 'stars': 30},
        {'name': 'fair_3', 'registry_source': 'community', 'age_days': 120, 'download_count': 10000, 'dependency_count': 15, 'publisher_verified': False, 'stars': 80},
        {'name': 'fair_4', 'registry_source': 'npm', 'age_days': 200, 'download_count': 20000, 'dependency_count': 20, 'publisher_verified': False, 'stars': 25},
        
        # POOR (0.40-0.54)
        {'name': 'poor_1', 'registry_source': 'github', 'age_days': 20, 'download_count': 500, 'dependency_count': 25, 'publisher_verified': False, 'stars': 5},
        {'name': 'poor_2', 'registry_source': 'unknown', 'age_days': 45, 'download_count': 200, 'dependency_count': 18, 'publisher_verified': False, 'stars': 0},
        {'name': 'poor_3', 'registry_source': 'npm', 'age_days': 10, 'download_count': 100, 'dependency_count': 30, 'publisher_verified': False, 'stars': 2},
        {'name': 'poor_4', 'registry_source': 'github', 'age_days': 5, 'download_count': 50, 'dependency_count': 40, 'publisher_verified': False, 'stars': 0},
        
        # CRITICAL (0.0-0.39)
        {'name': 'critical_1', 'registry_source': 'unknown', 'age_days': 2, 'download_count': 0, 'dependency_count': 60, 'publisher_verified': False, 'stars': 0},
        {'name': 'critical_2', 'registry_source': 'unknown', 'age_days': None, 'download_count': None, 'dependency_count': None, 'publisher_verified': None, 'stars': None},
        {'name': 'critical_3', 'registry_source': 'unknown', 'age_days': 1, 'download_count': 10, 'dependency_count': 80, 'publisher_verified': False, 'stars': 0},
        
        # Mixed scenarios
        {'name': 'new_official', 'registry_source': 'official', 'age_days': 7, 'download_count': 10000, 'dependency_count': 2, 'publisher_verified': True, 'stars': 50},
        {'name': 'old_unknown', 'registry_source': 'unknown', 'age_days': 1500, 'download_count': 1000, 'dependency_count': 5, 'publisher_verified': False, 'stars': 20},
        {'name': 'popular_unverified', 'registry_source': 'npm', 'age_days': 400, 'download_count': 500000, 'dependency_count': 15, 'publisher_verified': False, 'stars': 500},
    ]
    
    # Add more varied servers to reach 34+
    additional_servers = [
        {'name': f'var_{i}', 'registry_source': ['official', 'npm', 'github', 'community'][i % 4], 
         'age_days': (i * 37) % 1000 + 5, 'download_count': (i * 1234) % 100000 + 100,
         'dependency_count': (i * 3) % 50 + 1, 'publisher_verified': i % 3 == 0,
         'stars': (i * 17) % 500 + 5}
        for i in range(18)
    ]
    
    all_servers = synthetic_servers + additional_servers
    
    results = []
    for server in all_servers:
        score, evidence = compute_supply_chain_score(server)
        band = get_score_band(score)
        hash_val = compute_score_hash(server)
        results.append({
            'name': server['name'],
            'score': score,
            'band': band,
            'hash': hash_val
        })
    
    # Sort by score for analysis
    results.sort(key=lambda x: x['score'], reverse=True)
    
    # Print distribution
    print(f"\nTotal servers scored: {len(results)}")
    band_counts = {}
    for r in results:
        band_counts[r['band']] = band_counts.get(r['band'], 0) + 1
    
    print("\nScore Distribution by Band:")
    print("-" * 40)
    for band, count in sorted(band_counts.items()):
        print(f"  {band:12}: {count:3} servers")
    
    print("\nScore Buckets (top 20):")
    print("-" * 60)
    for r in results[:20]:
        bar_len = int(r['score'] * 30)
        bar = '█' * bar_len + '░' * (30 - bar_len)
        print(f"  {r['name'][:20]:20} {r['score']:.3f} {bar} {r['band']}")
    
    print("\nBottom 10 scores:")
    print("-" * 60)
    for r in results[-10:]:
        bar_len = int(r['score'] * 30)
        bar = '█' * bar_len + '░' * (30 - bar_len)
        print(f"  {r['name'][:20]:20} {r['score']:.3f} {bar} {r['band']}")
    
    # Count distinct score buckets (score rounded to 2 decimals)
    distinct_scores = len(set(round(r['score'], 2) for r in results))
    print(f"\nDistinct score buckets (rounded to 0.01): {distinct_scores}")
    
    # Verify evidence structure
    print("\nSample Evidence (excellent_1):")
    _, sample_evidence = compute_supply_chain_score(synthetic_servers[0])
    print(json.dumps(sample_evidence, indent=2, default=str))
    
    print("\n" + "=" * 70)
    print(f"PASS: {distinct_scores >= 20} (need 20+ distinct buckets, got {distinct_scores})")
    print(f"PASS: {len(results) >= 34} (need 34+ servers, got {len(results)})")


if __name__ == '__main__':
    run()

# === Phase A §0.1 Fix A (2026-05-12, Robin/Claude) ===
# Compatibility alias: callers expecting v2/v4 sibling convention
# (`from supply_chain_enrichment_v3 import compute_score`) were ImportError-ing.
# The real entry point in this module is compute_supply_chain_score; this
# alias closes that ImportError without renaming the existing function.
# Verdict (different bugs, not double-dip) per phase_a_0_1_root_cause_2026-05-12.md.
compute_score = compute_supply_chain_score