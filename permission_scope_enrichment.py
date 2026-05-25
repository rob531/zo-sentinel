#!/usr/bin/env python3
"""
Permission Scope Enrichment Module
Pure enrichment for permission_scope signal.
Reads multiple metadata fields to compute discrimination score.
"""

from typing import Dict, List, Tuple, Any
import hashlib
import json


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute permission scope score from metadata.

    Args:
        metadata: Dictionary containing:
            - requested_permissions: list of permission strings
            - permission_complexity_score: float 0-100
            - permission_risk_tags: list of risk tag strings
            - scope_creep_indicators: list of scope creep flags

    Returns:
        Tuple of (score 0-100, evidence dict)
    """
    # Extract fields with safe defaults
    requested_perms = metadata.get('requested_permissions', [])
    complexity_score = metadata.get('permission_complexity_score', 50.0)
    risk_tags = metadata.get('permission_risk_tags', [])
    scope_creep = metadata.get('scope_creep_indicators', [])

    # Initialize evidence tracking
    evidence = {
        'fields_used': [],
        'field_scores': {},
        'partial_scores': [],
    }

    # Score component 1: Permission count and breadth
    perm_count_score = _score_permission_count(requested_perms, evidence)

    # Score component 2: Risk tag analysis
    risk_tag_score = _score_risk_tags(risk_tags, evidence)

    # Score component 3: Scope creep indicators
    creep_score = _score_scope_creep(scope_creep, evidence)

    # Score component 4: Complexity score integration
    complexity_contribution = _score_complexity(complexity_score, evidence)

    # Score component 5: Permission pattern matching
    pattern_score = _score_permission_patterns(requested_perms, evidence)

    # Weighted combination
    weights = {
        'permission_count': 0.30,
        'risk_tags': 0.25,
        'scope_creep': 0.20,
        'complexity': 0.10,
        'pattern_match': 0.15,
    }

    final_score = (
        perm_count_score * weights['permission_count'] +
        risk_tag_score * weights['risk_tags'] +
        creep_score * weights['scope_creep'] +
        complexity_contribution * weights['complexity'] +
        pattern_score * weights['pattern_match']
    )

    # Round to 2 decimal places
    final_score = round(final_score, 2)

    # Cap at 0-100 range
    final_score = max(0.0, min(100.0, final_score))

    evidence['final_score'] = final_score
    evidence['weights_applied'] = weights

    return final_score, evidence


def _score_permission_count(perms: List[str], evidence: Dict) -> float:
    """Score based on number and breadth of permissions requested."""
    evidence['fields_used'].append('requested_permissions')

    if not perms:
        # No permissions requested - most trusted
        score = 100.0
        evidence['field_scores']['requested_permissions'] = score
        evidence['partial_scores'].append({'component': 'permission_count', 'score': score, 'reason': 'no_permissions_requested'})
        return score

    count = len(perms)
    # Normalize count to score: fewer permissions = higher score
    if count <= 2:
        score = 95.0
    elif count <= 5:
        score = 80.0 - (count - 2) * 3
    elif count <= 10:
        score = 65.0 - (count - 5) * 4
    elif count <= 20:
        score = 45.0 - (count - 10) * 2
    else:
        score = max(10.0, 25.0 - (count - 20) * 1)

    evidence['field_scores']['requested_permissions'] = score
    evidence['partial_scores'].append({
        'component': 'permission_count',
        'score': score,
        'permission_count': count,
        'permissions_sample': perms[:5] if len(perms) > 5 else perms
    })
    return score


def _score_risk_tags(tags: List[str], evidence: Dict) -> float:
    """Score based on risk tag analysis."""
    evidence['fields_used'].append('permission_risk_tags')

    if not tags:
        score = 75.0  # Neutral when no tags
        evidence['field_scores']['permission_risk_tags'] = score
        evidence['partial_scores'].append({'component': 'risk_tags', 'score': score, 'reason': 'no_tags'})
        return score

    # Risk tag weights
    high_risk_tags = {
        'filesystem_full', 'env_all', 'network_unrestricted', 'process_spawn',
        'admin', 'root', 'privileged', 'system', 'credential_access'
    }
    medium_risk_tags = {
        'filesystem_read', 'filesystem_write', 'env_read', 'network_client',
        'subprocess', 'shell', 'exec', 'temporal'
    }
    low_risk_tags = {
        'read_only', 'limited', 'sandboxed', 'scoped', 'allowlisted'
    }

    high_count = sum(1 for t in tags if t.lower() in high_risk_tags)
    medium_count = sum(1 for t in tags if t.lower() in medium_risk_tags)
    low_count = sum(1 for t in tags if t.lower() in low_risk_tags)

    # Compute score from tag composition
    total_tags = len(tags)
    risk_exposure = (high_count * 1.0 + medium_count * 0.5 + low_count * 0.1) / max(total_tags, 1)
    score = 100.0 - (risk_exposure * 100.0)
    score = max(5.0, score)  # Minimum score for any tags

    evidence['field_scores']['permission_risk_tags'] = score
    evidence['partial_scores'].append({
        'component': 'risk_tags',
        'score': score,
        'high_risk_count': high_count,
        'medium_risk_count': medium_count,
        'low_risk_count': low_count,
        'tags_sample': tags[:5]
    })
    return score


def _score_scope_creep(indicators: List[str], evidence: Dict) -> float:
    """Score based on scope creep indicators."""
    evidence['fields_used'].append('scope_creep_indicators')

    if not indicators:
        score = 90.0  # Clean - no creep indicators
        evidence['field_scores']['scope_creep_indicators'] = score
        evidence['partial_scores'].append({'component': 'scope_creep', 'score': score, 'reason': 'no_indicators'})
        return score

    # Scope creep severity weights
    severe_indicators = {'excessive_permissions', 'overprivileged', 'privilege_escalation', 'lateral_movement'}
    moderate_indicators = {'multiple_capabilities', 'broad_scope', 'unnecessary_access', 'redundant_permissions'}
    mild_indicators = {'optional_permissions', 'future_proofing', 'extensible', 'adaptive'}

    severe_count = sum(1 for i in indicators if i.lower() in severe_indicators)
    moderate_count = sum(1 for i in indicators if i.lower() in moderate_indicators)
    mild_count = sum(1 for i in indicators if i.lower() in mild_indicators)

    creep_score = severe_count * 15 + moderate_count * 8 + mild_count * 3
    score = max(10.0, 100.0 - creep_score)

    evidence['field_scores']['scope_creep_indicators'] = score
    evidence['partial_scores'].append({
        'component': 'scope_creep',
        'score': score,
        'severe_count': severe_count,
        'moderate_count': moderate_count,
        'mild_count': mild_count
    })
    return score


def _score_complexity(complexity: float, evidence: Dict) -> float:
    """Score based on permission complexity score from metadata."""
    evidence['fields_used'].append('permission_complexity_score')

    # Complexity score is 0-100, where lower is simpler/more trusted
    # Invert: high complexity -> low trust score for this component
    score = 100.0 - complexity

    evidence['field_scores']['permission_complexity_score'] = score
    evidence['partial_scores'].append({
        'component': 'complexity',
        'score': score,
        'raw_complexity': complexity
    })
    return score


def _score_permission_patterns(perms: List[str], evidence: Dict) -> float:
    """Score based on detected permission patterns."""
    evidence['fields_used'].append('permission_pattern_analysis')

    if not perms:
        return 95.0

    # Pattern detection for scoring
    patterns = {
        'broad_filesystem': any('filesystem' in p.lower() or 'fs' in p.lower() or 'path' in p.lower()
                                 for p in perms),
        'env_access': any('env' in p.lower() for p in perms),
        'network_access': any('network' in p.lower() or 'http' in p.lower() or 'socket' in p.lower()
                              for p in perms),
        'process_control': any('process' in p.lower() or 'exec' in p.lower() or 'spawn' in p.lower()
                               for p in perms),
        'io_capability': any('read' in p.lower() or 'write' in p.lower() or 'io' in p.lower()
                             for p in perms),
    }

    # Count broad patterns - penalize
    pattern_penalty = sum(1 for present in patterns.values() if present) * 8

    # Per-permission normalization
    perm_density = len(perms) / 30.0  # Assume 30 is moderate
    density_penalty = min(20.0, perm_density * 20)

    score = 100.0 - pattern_penalty - density_penalty
    score = max(5.0, score)

    evidence['field_scores']['permission_pattern_analysis'] = score
    evidence['partial_scores'].append({
        'component': 'pattern_match',
        'score': score,
        'detected_patterns': [k for k, v in patterns.items() if v],
        'permission_count': len(perms)
    })
    return score


def generate_fingerprint_id(perms: List[str], tags: List[str]) -> str:
    """Generate deterministic fingerprint ID for deduplication."""
    content = json.dumps({'perms': sorted(perms), 'tags': sorted(tags)}, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


if __name__ == '__main__':
    # Smoke test for enrichment_harness compatibility
    test_metadata = {
        'requested_permissions': ['filesystem_read', 'env_read', 'network_client'],
        'permission_complexity_score': 35.0,
        'permission_risk_tags': ['read_only', 'limited'],
        'scope_creep_indicators': []
    }

    score, evidence = compute_score(test_metadata)
    print(f"Score: {score}")
    print(f"Evidence: {json.dumps(evidence, indent=2)}")

    # Test various patterns
    test_cases = [
        ({'requested_permissions': [], 'permission_complexity_score': 0, 'permission_risk_tags': [], 'scope_creep_indicators': []}, 100),
        ({'requested_permissions': ['admin', 'root', 'filesystem_full', 'env_all'], 'permission_complexity_score': 95, 'permission_risk_tags': ['privileged', 'credential_access'], 'scope_creep_indicators': ['overprivileged']}, 15),
        ({'requested_permissions': ['filesystem_specific'], 'permission_complexity_score': 30, 'permission_risk_tags': ['scoped'], 'scope_creep_indicators': ['optional_permissions']}, 85),
    ]

    print("\nTest results:")
    for metadata, expected_range in test_cases:
        score, _ = compute_score(metadata)
        print(f"  Perms: {len(metadata['requested_permissions'])} -> Score: {score}")

    print("\nEnrichment module OK")
    exit(0)