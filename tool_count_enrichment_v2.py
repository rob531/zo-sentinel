"""Tool count enrichment module for improving signal discrimination."""


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute enrichment score based on tool_count and related metadata.
    
    Args:
        metadata: Dictionary containing tool metadata including:
            - tool_count: Number of tools
            - registry_source: Source of the registry
            - publisher_verified: Boolean indicating if publisher is verified
            - age_days: Age of the package in days
            - dependency_count: Number of dependencies
            - stars: Number of stars
            
    Returns:
        Tuple of (score, evidence_dict)
    """
    # Extract fields with safe defaults
    tool_count = metadata.get('tool_count', 0)
    publisher_verified = metadata.get('publisher_verified', False)
    age_days = metadata.get('age_days', float('inf'))
    dependency_count = metadata.get('dependency_count', 0)
    stars = metadata.get('stars', 0)
    
    # Initialize evidence dict
    evidence = {
        'tool_count': tool_count,
        'bucket': None,
        'bucket_base': 0,
        'overage_penalty': 0,
        'modifiers': {}
    }
    
    # Bucketing logic
    if 1 <= tool_count <= 5:
        # Micro: 1-5 tools
        base_score = 40.0
        evidence['bucket'] = 'micro'
        evidence['bucket_base'] = 40
    elif 6 <= tool_count <= 19:
        # Standard: 6-19 tools
        base_score = 65.0
        evidence['bucket'] = 'standard'
        evidence['bucket_base'] = 65
    else:
        # Large: 20+ tools
        base_score = 50.0
        evidence['bucket'] = 'large'
        evidence['bucket_base'] = 50
        # Penalty per tool over 25
        if tool_count > 25:
            overage = tool_count - 25
            penalty = overage
            base_score -= penalty
            evidence['overage_penalty'] = penalty
    
    # Apply modifiers
    modifiers = {}
    
    # publisher_verified: +15
    if publisher_verified:
        modifiers['publisher_verified'] = 15
        base_score += 15
    
    # recent age < 90 days: -10
    if age_days < 90:
        modifiers['recent_age'] = -10
        base_score -= 10
    
    # high dependency_count > 10: -15
    if dependency_count > 10:
        modifiers['high_dependency_count'] = -15
        base_score -= 15
    
    # high stars > 1000: +10
    if stars > 1000:
        modifiers['high_stars'] = 10
        base_score += 10
    
    evidence['modifiers'] = modifiers
    
    return base_score, evidence