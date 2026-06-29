def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Computes an enriched score for tool_count signal considering multiple metadata fields.
    Returns a tuple of (score, explanation_dict) where score is between 0-1 and
    explanation_dict contains the scoring factors.
    """
    # Extract and validate fields with defaults
    tool_count = metadata.get('tool_count', 0)
    registry_source = metadata.get('registry_source', 'unknown')
    publisher_verified = metadata.get('publisher_verified', False)
    stars = metadata.get('stars', 0)
    download_count = metadata.get('download_count', 0)
    age_days = metadata.get('age_days', 0)

    # Initialize score components
    score_components = {
        'tool_count': 0.0,
        'registry_source': 0.0,
        'publisher_verified': 0.0,
        'stars': 0.0,
        'download_count': 0.0,
        'age_days': 0.0
    }

    # Score tool_count with graduated penalties for extremes
    if tool_count <= 5:
        score_components['tool_count'] = 0.1
    elif tool_count <= 20:
        score_components['tool_count'] = 0.5
    elif tool_count <= 50:
        score_components['tool_count'] = 0.8
    elif tool_count <= 100:
        score_components['tool_count'] = 0.6
    else:
        score_components['tool_count'] = 0.2

    # Score registry_source
    if registry_source == 'official':
        score_components['registry_source'] = 0.3
    elif registry_source == 'community':
        score_components['registry_source'] = 0.2
    else:
        score_components['registry_source'] = 0.1

    # Score publisher_verified
    if publisher_verified:
        # Boost for verified publishers with moderate tool counts
        if 5 < tool_count <= 50:
            score_components['publisher_verified'] = 0.4
        else:
            score_components['publisher_verified'] = 0.2
    else:
        score_components['publisher_verified'] = 0.0

    # Score stars (normalized to 0-1)
    score_components['stars'] = min(stars / 100, 0.3)

    # Score download_count (normalized to 0-1 with diminishing returns)
    download_score = min(download_count / 10000, 0.3)
    score_components['download_count'] = download_score

    # Score age_days (older is better up to a point)
    if age_days <= 30:
        score_components['age_days'] = 0.0
    elif age_days <= 365:
        score_components['age_days'] = 0.2
    elif age_days <= 730:
        score_components['age_days'] = 0.3
    else:
        score_components['age_days'] = 0.1

    # Calculate final score (normalized to 0-1)
    total_score = sum(score_components.values())
    normalized_score = min(total_score, 1.0)

    # Prepare explanation
    explanation = {
        'tool_count': {
            'value': tool_count,
            'contribution': score_components['tool_count']
        },
        'registry_source': {
            'value': registry_source,
            'contribution': score_components['registry_source']
        },
        'publisher_verified': {
            'value': publisher_verified,
            'contribution': score_components['publisher_verified']
        },
        'stars': {
            'value': stars,
            'contribution': score_components['stars']
        },
        'download_count': {
            'value': download_count,
            'contribution': score_components['download_count']
        },
        'age_days': {
            'value': age_days,
            'contribution': score_components['age_days']
        },
        'total_score': total_score,
        'normalized_score': normalized_score
    }

    return (normalized_score, explanation)