#!/usr/bin/env python3
"""
Supply Chain Signal Enricher Module

Computes a supply chain trust score for MCP servers based on metadata fields
indicating dependency health, registry reputation, and published-by-trusted-source flags.
"""

from typing import Dict, List, Tuple, Any


# Registry source base scores
REGISTRY_SCORES = {
    'npm': 70,
    'pypi': 70,
    'nuget': 65,
    'huggingface': 75,
    'github': 80,
    'other': 40,
}

# Weight configuration
WEIGHTS = {
    'registry_source': 0.25,
    'publisher_verified': 0.15,
    'dependency_count': 0.15,
    'has_license': 0.10,
    'has_security_policy': 0.15,
    'has_code_of_conduct': 0.10,
    'repo_stars': 0.10,
}

# Verdict thresholds
VERDICT_THRESHOLDS = [
    (70, 'HIGH'),
    (45, 'MEDIUM'),
    (25, 'LOW'),
    (0, 'INSUFFICIENT'),
]

# Dependency count scoring bands
DEPENDENCY_BANDS = [
    (0, 0, 100),      # 0 deps = 100
    (1, 5, 80),       # 1-5 deps = 80
    (6, 20, 60),      # 6-20 deps = 60
    (21, 50, 40),     # 21-50 deps = 40
    (51, float('inf'), 20),  # 51+ deps = 20
]


def compute_dependency_score(dependency_count: int) -> float:
    """Calculate dependency count score based on bands."""
    for min_val, max_val, score in DEPENDENCY_BANDS:
        if min_val <= dependency_count <= max_val:
            return score
    return 20  # Default for very large counts


def compute_repo_stars_score(repo_stars: int) -> float:
    """
    Calculate repo_stars score based on star count.
    Uses a logarithmic-like scale: 0 stars = 0, 100+ stars = 100.
    """
    if repo_stars <= 0:
        return 0
    if repo_stars >= 100:
        return 100
    # Linear interpolation for 1-99 stars
    return repo_stars


def get_verdict(score: float) -> str:
    """Determine verdict based on score thresholds."""
    for threshold, verdict in VERDICT_THRESHOLDS:
        if score > threshold:
            return verdict
    return 'INSUFFICIENT'


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute supply chain signal score based on package metadata.
    
    Args:
        metadata: Dictionary containing optional fields:
            - registry_source (str): npm/pypi/nuget/huggingface/github/other
            - publisher_verified (bool): Whether publisher is verified
            - dependency_count (int): Number of dependencies
            - direct_dependencies (list): List of dependency dicts
            - has_license (bool): Whether package has a license
            - license_type (str): Type of license
            - repo_stars (int): Repository star count
            - has_security_policy (bool): Whether security policy exists
            - has_code_of_conduct (bool): Whether code of conduct exists
    
    Returns:
        Tuple of (score in 0..100, evidence dict with signal metadata)
    """
    score_breakdown = {}
    missing_fields = []
    present_fields = []
    
    total_weight = sum(WEIGHTS.values())
    weighted_score = 0.0
    
    # Registry source scoring (weight 0.25)
    registry_source = metadata.get('registry_source')
    if registry_source is not None:
        registry_score = REGISTRY_SCORES.get(registry_source.lower(), REGISTRY_SCORES['other'])
        weighted_contribution = registry_score * WEIGHTS['registry_source']
        score_breakdown['registry_source'] = {
            'raw_score': registry_score,
            'weight': WEIGHTS['registry_source'],
            'weighted_contribution': weighted_contribution,
        }
        weighted_score += weighted_contribution
        present_fields.append('registry_source')
    else:
        missing_fields.append('registry_source')
    
    # Publisher verified scoring (weight 0.15)
    publisher_verified = metadata.get('publisher_verified')
    if publisher_verified is not None:
        if publisher_verified:
            verified_score = 100  # +20 bonus
            weighted_contribution = 20 * WEIGHTS['publisher_verified']  # Base 80 + 20 bonus
        else:
            weighted_contribution = 80 * WEIGHTS['publisher_verified']
            verified_score = 80
        score_breakdown['publisher_verified'] = {
            'raw_score': verified_score if publisher_verified else 80,
            'bonus': 20 if publisher_verified else 0,
            'weight': WEIGHTS['publisher_verified'],
            'weighted_contribution': weighted_contribution,
        }
        weighted_score += weighted_contribution
        present_fields.append('publisher_verified')
    else:
        missing_fields.append('publisher_verified')
    
    # Dependency count scoring (weight 0.15)
    dependency_count = metadata.get('dependency_count')
    if dependency_count is not None:
        dep_score = compute_dependency_score(dependency_count)
        weighted_contribution = dep_score * WEIGHTS['dependency_count']
        score_breakdown['dependency_count'] = {
            'raw_score': dep_score,
            'count': dependency_count,
            'weight': WEIGHTS['dependency_count'],
            'weighted_contribution': weighted_contribution,
        }
        weighted_score += weighted_contribution
        present_fields.append('dependency_count')
    else:
        missing_fields.append('dependency_count')
    
    # Has license scoring (weight 0.10)
    has_license = metadata.get('has_license')
    if has_license is not None:
        license_score = 100 if has_license else 0
        weighted_contribution = license_score * WEIGHTS['has_license']
        score_breakdown['has_license'] = {
            'raw_score': license_score,
            'weight': WEIGHTS['has_license'],
            'weighted_contribution': weighted_contribution,
        }
        weighted_score += weighted_contribution
        present_fields.append('has_license')
    else:
        missing_fields.append('has_license')
    
    # Has security policy scoring (weight 0.15)
    has_security_policy = metadata.get('has_security_policy')
    if has_security_policy is not None:
        policy_score = 100 if has_security_policy else 0
        weighted_contribution = policy_score * WEIGHTS['has_security_policy']
        score_breakdown['has_security_policy'] = {
            'raw_score': policy_score,
            'weight': WEIGHTS['has_security_policy'],
            'weighted_contribution': weighted_contribution,
        }
        weighted_score += weighted_contribution
        present_fields.append('has_security_policy')
    else:
        missing_fields.append('has_security_policy')
    
    # Has code of conduct scoring (weight 0.10)
    has_code_of_conduct = metadata.get('has_code_of_conduct')
    if has_code_of_conduct is not None:
        coc_score = 100 if has_code_of_conduct else 0
        weighted_contribution = coc_score * WEIGHTS['has_code_of_conduct']
        score_breakdown['has_code_of_conduct'] = {
            'raw_score': coc_score,
            'weight': WEIGHTS['has_code_of_conduct'],
            'weighted_contribution': weighted_contribution,
        }
        weighted_score += weighted_contribution
        present_fields.append('has_code_of_conduct')
    else:
        missing_fields.append('has_code_of_conduct')
    
    # Repo stars scoring (weight 0.10)
    repo_stars = metadata.get('repo_stars')
    if repo_stars is not None:
        stars_score = compute_repo_stars_score(repo_stars)
        weighted_contribution = stars_score * WEIGHTS['repo_stars']
        score_breakdown['repo_stars'] = {
            'raw_score': stars_score,
            'stars': repo_stars,
            'weight': WEIGHTS['repo_stars'],
            'weighted_contribution': weighted_contribution,
        }
        weighted_score += weighted_contribution
        present_fields.append('repo_stars')
    else:
        missing_fields.append('repo_stars')
    
    # Calculate final score (normalize to 0-100)
    max_possible_score = 100 * total_weight
    final_score = (weighted_score / max_possible_score) * 100
    final_score = round(min(100.0, max(0.0, final_score)), 2)
    
    # Calculate confidence based on present fields
    all_fields = list(WEIGHTS.keys())
    confidence = len(present_fields) / len(all_fields)
    
    # Determine verdict
    verdict = get_verdict(final_score)
    
    # Build evidence dict
    evidence = {
        'signal_type': 'supply_chain',
        'confidence': round(confidence, 2),
        'evidence_blob': {
            'score_breakdown': score_breakdown,
            'registry_source': registry_source,
            'missing': missing_fields,
            'verdict': verdict,
        },
    }
    
    return final_score, evidence


def run_tests():
    """Run self-test cases."""
    all_passed = True
    
    # Test case 1: npm package with publisher verified and moderate dependencies
    print("Test 1: npm package with publisher verified, 5 deps, security policy")
    metadata1 = {
        'registry_source': 'npm',
        'publisher_verified': True,
        'dependency_count': 5,
        'has_security_policy': True,
    }
    score1, evidence1 = compute_score(metadata1)
    
    try:
        assert 0 <= score1 <= 100, f"Score {score1} out of range"
        assert 'verdict' in evidence1['evidence_blob'], "Missing verdict"
        print(f"  Score: {score1}, Verdict: {evidence1['evidence_blob']['verdict']}")
        print(f"  Missing fields: {evidence1['evidence_blob']['missing']}")
        print("  PASS")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_passed = False
    
    # Test case 2: Empty metadata
    print("\nTest 2: Empty metadata")
    metadata2 = {}
    score2, evidence2 = compute_score(metadata2)
    
    try:
        assert score2 == 0, f"Expected score 0, got {score2}"
        missing = evidence2['evidence_blob']['missing']
        expected_missing = list(WEIGHTS.keys())
        for field in expected_missing:
            assert field in missing, f"Field '{field}' not in missing list"
        print(f"  Score: {score2}, Missing fields: {missing}")
        print("  PASS")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_passed = False
    
    # Test case 3: GitHub package with high trust indicators
    print("\nTest 3: GitHub package with verified publisher, license, high stars")
    metadata3 = {
        'registry_source': 'github',
        'publisher_verified': True,
        'has_license': True,
        'repo_stars': 500,
    }
    score3, evidence3 = compute_score(metadata3)
    
    try:
        assert score3 > 70, f"Expected score > 70, got {score3}"
        print(f"  Score: {score3}, Verdict: {evidence3['evidence_blob']['verdict']}")
        print(f"  Missing fields: {evidence3['evidence_blob']['missing']}")
        print("  PASS")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        all_passed = False
    
    print("\n" + "=" * 50)
    if all_passed:
        print("All tests PASSED")
        return 0
    else:
        print("Some tests FAILED")
        return 1


if __name__ == '__main__':
    exit(run_tests())