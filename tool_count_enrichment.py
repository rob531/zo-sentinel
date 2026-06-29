import math
from typing import Dict, Tuple

def compute_score(metadata: Dict) -> Tuple[float, Dict]:
    """
    Compute an enriched score for tool_count signal using multiple metadata fields.

    Args:
        metadata: Dictionary containing tool metadata with keys:
            - registry_source (str)
            - description_length (int)
            - dependency_count (int)
            - stars (int)

    Returns:
        Tuple of (score: float, explanation: Dict)
    """
    # Extract and normalize each component
    registry_scores = {
        'npm': 0.8,
        'pypi': 0.7,
        'rubygems': 0.6,
        'packagist': 0.5,
        'maven': 0.4,
        'default': 0.3
    }
    registry_source = metadata.get('registry_source', 'default')
    registry_score = registry_scores.get(registry_source, registry_scores['default'])

    # Normalize description length (0-1)
    max_desc_len = 1000
    desc_len = min(metadata.get('description_length', 0), max_desc_len)
    desc_score = desc_len / max_desc_len

    # Normalize dependency count (0-1) with log scaling
    max_deps = 100
    deps = min(metadata.get('dependency_count', 0), max_deps)
    dep_score = math.log1p(deps) / math.log1p(max_deps) if max_deps > 0 else 0

    # Normalize stars (0-1) with log scaling
    max_stars = 10000
    stars = min(metadata.get('stars', 0), max_stars)
    star_score = math.log1p(stars) / math.log1p(max_stars) if max_stars > 0 else 0

    # Combine components with different weights
    weights = {
        'registry': 0.3,
        'description': 0.2,
        'dependencies': 0.3,
        'stars': 0.2
    }

    # Calculate weighted sum
    score = (
        registry_score * weights['registry'] +
        desc_score * weights['description'] +
        dep_score * weights['dependencies'] +
        star_score * weights['stars']
    )

    # Scale to 0-100 range and round to 2 decimal places
    final_score = round(score * 100, 2)

    # Create explanation dictionary
    explanation = {
        'registry_source': registry_source,
        'registry_score': round(registry_score, 2),
        'description_length': desc_len,
        'description_score': round(desc_score, 2),
        'dependency_count': deps,
        'dependency_score': round(dep_score, 2),
        'stars': stars,
        'star_score': round(star_score, 2),
        'weights': weights,
        'raw_score': round(score, 2),
        'final_score': final_score
    }

    return final_score, explanation