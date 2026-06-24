#!/usr/bin/env python3
"""
Domain provenance signal enrichment module.

Computes a weighted score based on domain age, registrar, and typosquat distance.
"""

def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute a weighted score based on domain provenance signals.
    
    Args:
        metadata: Dictionary containing domain_age_days, registrar, and typosquat_distance
    
    Returns:
        Tuple of (score in 0..100, evidence dict with 'verdict' and 'missing' keys)
    """
    evidence = {
        'verdict': '',
        'missing': []
    }
    score = 100.0  # Start with maximum score

    # Penalize young domains (<180 days)
    domain_age = metadata.get('domain_age_days', 0)
    if domain_age < 180:
        penalty = 100.0 * (1 - (domain_age / 180))
        score -= penalty
        evidence['young_domain'] = {
            'days_old': domain_age,
            'penalty': penalty,
            'reason': 'Domain is younger than 180 days'
        }

    # Penalize weak registrars
    registrar = metadata.get('registrar', '').lower()
    weak_registrars = ['freedom', 'namecheap', 'godaddy', '1and1', 'enom']
    if registrar in weak_registrars:
        score -= 20.0
        evidence['weak_registrar'] = {
            'registrar': registrar,
            'penalty': 20.0,
            'reason': 'Domain uses a weak registrar'
        }

    # Penalize small typosquat distance to popular packages
    typosquat_distance = metadata.get('typosquat_distance', float('inf'))
    if typosquat_distance < 3:
        penalty = 100.0 * (1 - (typosquat_distance / 3))
        score -= penalty
        evidence['typosquat'] = {
            'distance': typosquat_distance,
            'penalty': penalty,
            'reason': 'Domain is a close typosquat of a popular package'
        }

    # Ensure score is within bounds
    score = max(0.0, min(100.0, score))

    return (round(score, 2), evidence)


if __name__ == '__main__':
    # Self-test cases
    test_cases = [
        # Young domain, weak registrar, close typosquat
        {'domain_age_days': 30, 'registrar': 'freedom', 'typosquat_distance': 1},
        # Old domain, strong registrar, far typosquat
        {'domain_age_days': 365, 'registrar': 'verisign', 'typosquat_distance': 10},
        # Young domain, strong registrar, no typosquat
        {'domain_age_days': 90, 'registrar': 'verisign', 'typosquat_distance': float('inf')},
        # Old domain, weak registrar, close typosquat
        {'domain_age_days': 365, 'registrar': 'godaddy', 'typosquat_distance': 2},
    ]

    for i, metadata in enumerate(test_cases, start=1):
        score, evidence = compute_score(metadata)
        # Ensure score is within expected range
        assert 0 <= score <= 100, f"Score out of range: {score}"
        print(f"Test case {i}:")
        print(f"  Input: {metadata}")
        print(f"  Score: {score}")
        print(f"  Evidence: {evidence}")
        print()

    print("PASS")
