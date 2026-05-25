"""
Registry Breadth Enrichment
Scores how many independent registries observed this MCP server.
"""

SIGNAL_NAME = "registry_breadth"
VERSION = "1.0.0"
MAX_SCORE = 100.0

REGISTRY_FIELDS = [
    "in_glama",
    "in_smithery",
    "in_pulse",
    "in_npm",
    "in_pypi",
    "in_github",
]

REGISTRY_COUNT_SCORES = {
    0: 0.0,
    1: 30.0,
    2: 50.0,
    3: 70.0,
    4: 85.0,
    5: 92.0,
    6: 95.0,
}

YOUNG_SERVER_AGE_THRESHOLD_DAYS = 14
YOUNG_SINGLE_REGISTRY_CAP = 25.0
VERIFICATION_BONUS = 10.0


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute registry breadth score for an MCP server.
    
    Args:
        metadata: Dictionary containing registry membership flags and server age.
        
    Returns:
        Tuple of (score, evidence_dict) where evidence contains per-registry contributions
        and cap reasons.
    """
    registries_observed = []
    for field in REGISTRY_FIELDS:
        if metadata.get(field, False):
            registries_observed.append(field)
    
    registry_count = len(registries_observed)
    is_verified = metadata.get("publisher_verified", False)
    age_days = metadata.get("age_days", 0) or 0
    
    base_score = REGISTRY_COUNT_SCORES.get(registry_count, 0.0)
    
    verification_bonus = 0.0
    if is_verified:
        verification_bonus = VERIFICATION_BONUS
    
    score = base_score + verification_bonus
    
    cap_reason = None
    
    if registry_count == 1 and age_days < YOUNG_SERVER_AGE_THRESHOLD_DAYS:
        score = min(score, YOUNG_SINGLE_REGISTRY_CAP)
        cap_reason = f"Single registry with age < {YOUNG_SERVER_AGE_THRESHOLD_DAYS} days - likely fresh and unattested"
    elif registry_count == 1 and not is_verified:
        cap_reason = "Single registry, no publisher verification - thin attestation signal"
    elif registry_count >= 4 and is_verified:
        cap_reason = "Corroborated existence: 4+ registries with publisher verification"
    elif registry_count >= 4:
        cap_reason = f"Multi-registry corroboration: {registry_count} independent sources"
    elif registry_count >= 2:
        cap_reason = f"Limited corroboration: {registry_count} registries"
    
    evidence = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "registry_count": registry_count,
        "registries_observed": registries_observed,
        "in_glama": metadata.get("in_glama", False),
        "in_smithery": metadata.get("in_smithery", False),
        "in_pulse": metadata.get("in_pulse", False),
        "in_npm": metadata.get("in_npm", False),
        "in_pypi": metadata.get("in_pypi", False),
        "in_github": metadata.get("in_github", False),
        "publisher_verified": is_verified,
        "age_days": age_days,
        "age_threshold_days": YOUNG_SERVER_AGE_THRESHOLD_DAYS,
        "is_young_server": age_days < YOUNG_SERVER_AGE_THRESHOLD_DAYS,
        "base_score": base_score,
        "verification_bonus": verification_bonus,
        "score_before_cap": base_score + verification_bonus,
        "cap_reason": cap_reason,
        "cap_applied": cap_reason is not None,
        "final_score": score,
    }
    
    return round(score, 1), evidence


if __name__ == "__main__":
    test_cases = [
        {
            "name": "Single registry, young server (cap applied)",
            "metadata": {
                "in_glama": True,
                "in_smithery": False,
                "in_pulse": False,
                "in_npm": False,
                "in_pypi": False,
                "in_github": False,
                "publisher_verified": False,
                "age_days": 7,
            },
        },
        {
            "name": "Single registry, no verification",
            "metadata": {
                "in_glama": False,
                "in_smithery": True,
                "in_pulse": False,
                "in_npm": False,
                "in_pypi": False,
                "in_github": False,
                "publisher_verified": False,
                "age_days": 90,
            },
        },
        {
            "name": "Single registry, verified publisher",
            "metadata": {
                "in_glama": False,
                "in_smithery": True,
                "in_pulse": False,
                "in_npm": False,
                "in_pypi": False,
                "in_github": False,
                "publisher_verified": True,
                "age_days": 90,
            },
        },
        {
            "name": "Two registries, no verification",
            "metadata": {
                "in_glama": True,
                "in_smithery": True,
                "in_pulse": False,
                "in_npm": False,
                "in_pypi": False,
                "in_github": False,
                "publisher_verified": False,
                "age_days": 60,
            },
        },
        {
            "name": "Four registries, verified",
            "metadata": {
                "in_glama": True,
                "in_smithery": True,
                "in_pulse": True,
                "in_npm": True,
                "in_pypi": False,
                "in_github": False,
                "publisher_verified": True,
                "age_days": 180,
            },
        },
        {
            "name": "Six registries (all), verified",
            "metadata": {
                "in_glama": True,
                "in_smithery": True,
                "in_pulse": True,
                "in_npm": True,
                "in_pypi": True,
                "in_github": True,
                "publisher_verified": True,
                "age_days": 365,
            },
        },
    ]
    
    print(f"Registry Breadth Enrichment v{VERSION}")
    print("=" * 60)
    
    for tc in test_cases:
        score, evidence = compute_score(tc["metadata"])
        print(f"\nTest: {tc['name']}")
        print(f"  Score: {score}")
        print(f"  Registries ({evidence['registry_count']}): {evidence['registries_observed']}")
        print(f"  Verified: {evidence['publisher_verified']}, Age: {evidence['age_days']} days")
        print(f"  Cap Applied: {evidence['cap_applied']} - {evidence['cap_reason']}")