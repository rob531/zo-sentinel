#!/usr/bin/env python3
"""
Supply Chain Threat Enrichment Module

Pure enrichment module exposing compute_score(metadata: dict) -> (float, dict)
for supply-chain threat signals.

Scores an MCP's supply-chain health based on registry_source, publisher_verified,
dependency_count, download_count, stars, age_days, and external threat intelligence flags.
Produces a normalized 0-100 score with evidence.
"""

from __future__ import annotations

import math
from typing import Any


# Registry reputation scores (higher = more trusted)
REGISTRY_REPUTATION_SCORES: dict[str, float] = {
    'github': 1.0,
    'npm': 0.8,
    'pypi': 0.7,
    'cargo': 0.6,
    'nuget': 0.5,
    'docker': 0.5,
    'unknown': 0.2,
}

# Threat flag severity mapping
THREAT_FLAG_SEVERITY: dict[str, int] = {
    'known_malware': 100,
    'typosquatting': 80,
    'dependency_confusion': 70,
    'supply_chain_attack': 90,
    'abandoned': 40,
    'vulnerable': 60,
    'suspicious_activity': 50,
    'recent_compromise': 95,
    'unmaintained': 30,
    'license_violation': 20,
}

# Fixed weights that sum to 1.0
WEIGHTS = {
    'publisher_verified': 0.25,
    'registry_reputation': 0.20,
    'community_signal': 0.20,
    'supply_chain_complexity': 0.15,
    'temporal_stability': 0.10,
    'threat_flag_penalty': 0.10,
}

# Expected input fields (required)
REQUIRED_FIELDS = [
    'registry_source',
    'publisher_verified',
    'dependency_count',
    'download_count',
    'stars',
    'age_days',
]

# Optional fields
OPTIONAL_FIELDS = [
    'threat_flags',
    'maintained',
]


def _normalize_community_signal(stars: int, download_count: int) -> float:
    """
    Normalize community signal from stars and downloads.
    Uses log scale to handle wide range of values.
    Returns value in range [0.0, 1.0].
    """
    # Log-scaled normalization for downloads (common range: 0 to millions)
    if download_count > 0:
        # log10(1) = 0, log10(10M) ≈ 7
        download_score = min(math.log10(download_count + 1) / 7.0, 1.0)
    else:
        download_score = 0.0

    # Log-scaled normalization for stars (common range: 0 to 100k+)
    if stars > 0:
        # log10(1) = 0, log10(100k) ≈ 5
        stars_score = min(math.log10(stars + 1) / 5.0, 1.0)
    else:
        stars_score = 0.0

    # Weighted combination: downloads weighted higher as they're more concrete
    return (download_score * 0.6) + (stars_score * 0.4)


def _normalize_supply_chain_complexity(dependency_count: int) -> float:
    """
    Normalize supply chain complexity.
    Fewer dependencies = lower risk = higher score.
    Returns value in range [0.0, 1.0].
    """
    if dependency_count <= 0:
        return 1.0  # No dependencies = minimal attack surface
    elif dependency_count <= 5:
        return 1.0 - (dependency_count * 0.05)  # 1.0 to 0.75
    elif dependency_count <= 20:
        return 0.75 - ((dependency_count - 5) * 0.02)  # 0.75 to 0.45
    elif dependency_count <= 100:
        return 0.45 - ((dependency_count - 20) * 0.005)  # 0.45 to 0.15
    else:
        return max(0.05, 0.15 - ((dependency_count - 100) * 0.001))


def _normalize_temporal_stability(age_days: int) -> float:
    """
    Normalize temporal stability based on package age.
    Older packages are considered more battle-tested.
    Returns value in range [0.0, 1.0].
    """
    if age_days <= 0:
        return 0.0  # New or unknown age = high uncertainty
    elif age_days < 30:
        return 0.1  # Less than a month = very new
    elif age_days < 90:
        return 0.3  # Less than 3 months
    elif age_days < 180:
        return 0.5  # Less than 6 months
    elif age_days < 365:
        return 0.7  # Less than a year
    elif age_days < 730:
        return 0.85  # 1-2 years
    else:
        return 1.0  # 2+ years = well established


def _compute_threat_penalty(threat_flags: list[str]) -> tuple[float, list[str]]:
    """
    Compute penalty based on threat flags.
    Returns (penalty_score, threat_indicators) where penalty is [0.0, 1.0].
    """
    if not threat_flags:
        return 1.0, []  # No penalty, no indicators

    threat_indicators: list[str] = []
    max_severity = 0

    for flag in threat_flags:
        flag_lower = flag.lower().strip()
        if flag_lower in THREAT_FLAG_SEVERITY:
            severity = THREAT_FLAG_SEVERITY[flag_lower]
            threat_indicators.append(flag_lower)
            max_severity = max(max_severity, severity)
        else:
            # Unknown threat flag - still flag it
            threat_indicators.append(flag_lower)
            max_severity = max(max_severity, 50)  # Default unknown severity

    # Convert severity to penalty score (100 = max penalty = 0.0 score)
    # Lower score = worse
    penalty_score = 1.0 - (max_severity / 100.0)
    return penalty_score, threat_indicators


def _determine_verdict(score: float) -> str:
    """
    Determine risk verdict based on final score.
    """
    if score >= 80.0:
        return 'low_risk'
    elif score >= 60.0:
        return 'medium_risk'
    elif score >= 40.0:
        return 'high_risk'
    else:
        return 'critical'


def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compute supply-chain threat score from package metadata.

    Args:
        metadata: Dictionary containing package metadata fields

    Returns:
        Tuple of (score in range [0.0, 100.0], evidence dict)
    """
    # Track missing fields
    missing: list[str] = []

    # Initialize score breakdown
    score_breakdown: dict[str, float] = {}

    # Extract fields with defaults for missing
    registry_source = metadata.get('registry_source', 'unknown')
    if registry_source not in REGISTRY_REPUTATION_SCORES:
        registry_source = 'unknown'

    publisher_verified = metadata.get('publisher_verified')
    if publisher_verified is None:
        missing.append('publisher_verified')
        publisher_verified = False

    dependency_count = metadata.get('dependency_count')
    if dependency_count is None:
        missing.append('dependency_count')
        dependency_count = 0

    download_count = metadata.get('download_count')
    if download_count is None:
        missing.append('download_count')
        download_count = 0

    stars = metadata.get('stars')
    if stars is None:
        missing.append('stars')
        stars = 0

    age_days = metadata.get('age_days')
    if age_days is None:
        missing.append('age_days')
        age_days = 0

    threat_flags = metadata.get('threat_flags', [])
    if threat_flags is None:
        missing.append('threat_flags')
        threat_flags = []

    maintained = metadata.get('maintained')
    if maintained is None:
        missing.append('maintained')

    # Compute individual signal scores
    # 1. Publisher verified (0.25 weight)
    publisher_score = 1.0 if publisher_verified else 0.0
    score_breakdown['publisher_verified'] = publisher_score * WEIGHTS['publisher_verified']

    # 2. Registry reputation (0.20 weight)
    registry_score = REGISTRY_REPUTATION_SCORES.get(registry_source, 0.2)
    score_breakdown['registry_reputation'] = registry_score * WEIGHTS['registry_reputation']

    # 3. Community signal (0.20 weight)
    community_score = _normalize_community_signal(stars, download_count)
    score_breakdown['community_signal'] = community_score * WEIGHTS['community_signal']

    # 4. Supply chain complexity (0.15 weight)
    complexity_score = _normalize_supply_chain_complexity(dependency_count)
    score_breakdown['supply_chain_complexity'] = complexity_score * WEIGHTS['supply_chain_complexity']

    # 5. Temporal stability (0.10 weight)
    temporal_score = _normalize_temporal_stability(age_days)
    score_breakdown['temporal_stability'] = temporal_score * WEIGHTS['temporal_stability']

    # 6. Threat flag penalty (0.10 weight)
    threat_penalty_score, threat_indicators = _compute_threat_penalty(threat_flags)
    score_breakdown['threat_flag_penalty'] = threat_penalty_score * WEIGHTS['threat_flag_penalty']

    # Sum weighted scores to get final score
    total_score = sum(score_breakdown.values())

    # Scale to 0-100 range
    final_score = round(total_score * 100.0, 2)

    # Ensure bounds
    final_score = max(0.0, min(100.0, final_score))

    # Determine verdict
    verdict = _determine_verdict(final_score)

    # Build evidence dict
    evidence: dict[str, Any] = {
        'verdict': verdict,
        'missing': missing,
        'score_breakdown': score_breakdown,
        'threat_indicators': threat_indicators,
    }

    return final_score, evidence


if __name__ == '__main__':
    # Test cases
    test_cases = [
        {
            'name': 'Test 1: Full metadata (npm package)',
            'input': {
                'registry_source': 'npm',
                'publisher_verified': True,
                'dependency_count': 3,
                'download_count': 100000,
                'stars': 500,
                'age_days': 730,
            },
        },
        {
            'name': 'Test 2: Minimal input (github)',
            'input': {
                'registry_source': 'github',
            },
        },
        {
            'name': 'Test 3: PyPI with threat flag',
            'input': {
                'registry_source': 'pypi',
                'threat_flags': ['known_malware'],
            },
        },
    ]

    for test in test_cases:
        print(f"\n{test['name']}:")
        print(f"  Input: {test['input']}")

        score, evidence = compute_score(test['input'])

        print(f"  Score: {score}")
        print(f"  Verdict: {evidence['verdict']}")
        print(f"  Missing: {evidence['missing']}")
        print(f"  Threat Indicators: {evidence['threat_indicators']}")
        print(f"  Score Breakdown: {evidence['score_breakdown']}")

        # Assertions
        assert 0.0 <= score <= 100.0, f"Score {score} out of range [0, 100]"
        assert 'verdict' in evidence, "Missing 'verdict' in evidence"
        assert 'missing' in evidence, "Missing 'missing' in evidence"
        assert 'score_breakdown' in evidence, "Missing 'score_breakdown' in evidence"
        assert 'threat_indicators' in evidence, "Missing 'threat_indicators' in evidence"

        print("  PASS")