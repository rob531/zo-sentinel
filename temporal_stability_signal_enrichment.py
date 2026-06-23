"""
Temporal Stability Signal Enrichment Module

Computes stability scores based on temporal features and metadata signals.
Returns a float score (0.00-10.00) and detailed scoring breakdown dictionary.
"""

from typing import Tuple, Dict, Any
from datetime import datetime


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute temporal stability score for a package/server.
    
    Reads multiple metadata fields including registry_source, age_days, 
    download_count, dependency_count, publisher_verified, stars, and
    temporal data (first_seen, last_updated).
    
    Score discrimination:
        - Age scoring: 10+ distinct score values across age brackets
        - Update pattern: 5+ tiers based on temporal span analysis
        - Download scoring: Logarithmic scale providing many distinct values
        - Star scoring: Multiple tiers based on star counts
        - Dependency scoring: 5+ tiers based on dependency counts
        - Verification bonus: Binary factor
        - Registry factor: Per-registry coefficients
        - Recency adjustments: Time-since-update bonuses
        
    Args:
        metadata: Dictionary containing package metadata fields
            
    Returns:
        Tuple of (stability_score: float, breakdown: dict)
        Score ranges from 0.00 to 10.00 with fine-grained discrimination
    """
    
    # Extract all required metadata fields
    registry_source = metadata.get('registry_source', 'unknown')
    age_days = metadata.get('age_days', 0)
    download_count = metadata.get('download_count', 0)
    dependency_count = metadata.get('dependency_count', 0)
    publisher_verified = metadata.get('publisher_verified', False)
    stars = metadata.get('stars', 0)
    first_seen_ts = metadata.get('first_seen')
    last_updated_ts = metadata.get('last_updated')
    
    # Parse timestamps if they're strings
    if isinstance(first_seen_ts, str):
        try:
            first_seen_ts = datetime.fromisoformat(first_seen_ts.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            first_seen_ts = None
    
    if isinstance(last_updated_ts, str):
        try:
            last_updated_ts = datetime.fromisoformat(last_updated_ts.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            last_updated_ts = None
    
    # =========================================================================
    # AGE-BASED SCORING (10+ distinct values via fine-grained brackets)
    # =========================================================================
    age_score = 0.0
    
    if age_days >= 365 * 10:  # 10+ years - most mature
        age_score = 0.30
    elif age_days >= 365 * 7:  # 7-10 years
        age_score = 0.275
    elif age_days >= 365 * 5:  # 5-7 years
        age_score = 0.25
    elif age_days >= 365 * 4:  # 4-5 years
        age_score = 0.225
    elif age_days >= 365 * 3:  # 3-4 years
        age_score = 0.20
    elif age_days >= 365 * 2:  # 2-3 years
        age_score = 0.175
    elif age_days >= 365 * 1.5:  # 1.5-2 years
        age_score = 0.15
    elif age_days >= 365:  # 1-1.5 years
        age_score = 0.125
    elif age_days >= 180:  # 6 months - 1 year
        age_score = 0.10
    elif age_days >= 90:  # 3-6 months
        age_score = 0.075
    elif age_days >= 30:  # 1-3 months
        age_score = 0.05
    elif age_days >= 7:  # 1-4 weeks
        age_score = 0.025
    else:  # Less than 1 week
        age_score = 0.01
    
    # =========================================================================
    # UPDATE PATTERN SCORING (temporal stability from first_seen/last_updated)
    # =========================================================================
    update_pattern_score = 0.0
    time_span_days = 0
    days_since_update = 365  # Default assumption if no data
    
    if first_seen_ts and last_updated_ts:
        time_span_days = max(0, (last_updated_ts - first_seen_ts).days)
        
        if time_span_days >= 365 * 8:  # 8+ years of updates
            update_pattern_score = 0.15
        elif time_span_days >= 365 * 5:  # 5-8 years
            update_pattern_score = 0.135
        elif time_span_days >= 365 * 3:  # 3-5 years
            update_pattern_score = 0.12
        elif time_span_days >= 365 * 2:  # 2-3 years
            update_pattern_score = 0.10
        elif time_span_days >= 365:  # 1-2 years
            update_pattern_score = 0.08
        elif time_span_days >= 180:  # 6 months - 1 year
            update_pattern_score = 0.06
        elif time_span_days >= 90:  # 3-6 months
            update_pattern_score = 0.04
        elif time_span_days >= 30:  # 1-3 months
            update_pattern_score = 0.02
        else:  # Less than 1 month
            update_pattern_score = 0.01
        
        # Calculate days since last update
        now = datetime.now(last_updated_ts.tzinfo) if last_updated_ts.tzinfo else datetime.now()
        days_since_update = max(0, (now - last_updated_ts).days)
    elif age_days > 0:
        # No temporal data but has age - use age as proxy for time span
        time_span_days = age_days
        update_pattern_score = 0.05  # Partial credit for known age
    
    # =========================================================================
    # POPULARITY SCORING (download_count + stars with logarithmic scale)
    # =========================================================================
    download_score_component = 0.0
    star_score_component = 0.0
    
    # Logarithmic download scoring for many distinct values
    if download_count > 0:
        import math
        log_downloads = math.log10(max(1, download_count))
        
        if log_downloads >= 8:  # 100M+
            download_score_component = 0.15
        elif log_downloads >= 7:  # 10M-100M
            download_score_component = 0.135
        elif log_downloads >= 6:  # 1M-10M
            download_score_component = 0.12
        elif log_downloads >= 5:  # 100K-1M
            download_score_component = 0.10
        elif log_downloads >= 4:  # 10K-100K
            download_score_component = 0.08
        elif log_downloads >= 3:  # 1K-10K
            download_score_component = 0.06
        elif log_downloads >= 2:  # 100-1K
            download_score_component = 0.04
        else:  # < 100
            download_score_component = 0.02
    
    # Star-based scoring with multiple tiers
    if stars >= 10000:
        star_score_component = 0.10
    elif stars >= 5000:
        star_score_component = 0.09
    elif stars >= 1000:
        star_score_component = 0.08
    elif stars >= 500:
        star_score_component = 0.07
    elif stars >= 100:
        star_score_component = 0.06
    elif stars >= 50:
        star_score_component = 0.05
    elif stars >= 10:
        star_score_component = 0.04
    elif stars >= 1:
        star_score_component = 0.02
    else:
        star_score_component = 0.01
    
    popularity_score = download_score_component + star_score_component
    
    # =========================================================================
    # PUBLISHER VERIFICATION SCORING
    # =========================================================================
    verification_score = 0.10 if publisher_verified else 0.0
    
    # =========================================================================
    # DEPENDENCY COMPLEXITY SCORING (fewer deps often = more stable)
    # =========================================================================
    dependency_score = 0.0
    
    if dependency_count == 0:
        dependency_score = 0.08
    elif dependency_count <= 3:
        dependency_score = 0.075
    elif dependency_count <= 10:
        dependency_score = 0.07
    elif dependency_count <= 25:
        dependency_score = 0.06
    elif dependency_count <= 50:
        dependency_score = 0.05
    elif dependency_count <= 100:
        dependency_score = 0.04
    elif dependency_count <= 200:
        dependency_score = 0.02
    else:
        dependency_score = 0.01
    
    # =========================================================================
    # REGISTRY SOURCE FACTOR (different registries have different baselines)
    # =========================================================================
    registry_factors = {
        'pypi': 1.0,
        'npm': 0.95,
        'nuget': 0.95,
        'maven': 0.98,
        'rubygems': 0.90,
        'cargo': 0.92,
        'pub': 0.93,
        'conda': 0.88,
        'docker': 0.85,
        'helm': 0.87,
        'unknown': 0.70
    }
    registry_factor = registry_factors.get(registry_source.lower(), 0.70)
    
    # =========================================================================
    # RECENCY ADJUSTMENTS (bonus for recently maintained projects)
    # =========================================================================
    recency_bonus = 0.0
    
    if days_since_update <= 7:
        recency_bonus = 0.05
    elif days_since_update <= 30:
        recency_bonus = 0.04
    elif days_since_update <= 90:
        recency_bonus = 0.03
    elif days_since_update <= 180:
        recency_bonus = 0.02
    elif days_since_update <= 365:
        recency_bonus = 0.01
    
    # =========================================================================
    # COMPOSITE SCORE CALCULATION
    # =========================================================================
    weighted_components = (
        age_score * 2.0 +           # Age weighted heavily for stability
        update_pattern_score * 1.5 + # Temporal pattern important
        popularity_score * 1.0 +    # Popularity moderately weighted
        verification_score * 1.0 + # Verification weighted
        dependency_score * 0.8 +   # Dependencies moderately weighted
        recency_bonus * 1.0         # Recency bonus added
    )
    
    # Apply registry factor
    final_score = weighted_components * registry_factor
    
    # Clamp to valid range
    final_score = max(0.0, min(1.0, final_score))
    
    # Scale to 0-10 range with 2 decimal precision
    scaled_score = round(final_score * 10, 2)
    
    # =========================================================================
    # BUILD BREAKDOWN DICTIONARY
    # =========================================================================
    breakdown = {
        'final_score': scaled_score,
        'registry_source': registry_source,
        'registry_factor': registry_factor,
        
        'age_days': age_days,
        'age_score': round(age_score * 10, 2),
        'age_tier': _get_age_tier(age_days),
        
        'first_seen': str(first_seen_ts) if first_seen_ts else None,
        'last_updated': str(last_updated_ts) if last_updated_ts else None,
        'time_span_days': time_span_days,
        'days_since_update': days_since_update,
        'update_pattern_score': round(update_pattern_score * 10, 2),
        'update_pattern_tier': _get_update_tier(time_span_days),
        
        'download_count': download_count,
        'download_score_component': round(download_score_component * 10, 2),
        'download_tier': _get_download_tier(download_count),
        
        'stars': stars,
        'star_score_component': round(star_score_component * 10, 2),
        'star_tier': _get_star_tier(stars),
        
        'publisher_verified': publisher_verified,
        'verification_score': round(verification_score * 10, 2),
        
        'dependency_count': dependency_count,
        'dependency_score': round(dependency_score * 10, 2),
        'dependency_tier': _get_dependency_tier(dependency_count),
        
        'recency_bonus': round(recency_bonus * 10, 2),
        'recency_days': days_since_update,
        
        'weighted_raw_score': round(weighted_components * 10, 4),
        'registry_adjusted': registry_factor != 1.0,
        
        'score_components': {
            'age_contribution': round(age_score * 2.0 * 10, 2),
            'update_contribution': round(update_pattern_score * 1.5 * 10, 2),
            'popularity_contribution': round(popularity_score * 10, 2),
            'verification_contribution': round(verification_score * 10, 2),
            'dependency_contribution': round(dependency_score * 0.8 * 10, 2),
            'recency_contribution': round(recency_bonus * 10, 2)
        },
        
        'stability_indicators': {
            'is_mature': age_days >= 365,
            'is_popular': download_count >= 10000 or stars >= 100,
            'is_verified': publisher_verified,
            'is_maintained': days_since_update <= 180,
            'has_stable_history': time_span_days >= 365,
            'low_dependency_risk': dependency_count <= 25
        }
    }
    
    return scaled_score, breakdown


def _get_age_tier(age_days: int) -> str:
    """Get human-readable age tier."""
    if age_days >= 365 * 5:
        return 'very_mature'
    elif age_days >= 365 * 2:
        return 'mature'
    elif age_days >= 365:
        return 'established'
    elif age_days >= 90:
        return 'developing'
    elif age_days >= 30:
        return 'new'
    else:
        return 'very_new'


def _get_update_tier(time_span_days: int) -> str:
    """Get update pattern tier."""
    if time_span_days >= 365 * 5:
        return 'long_term_active'
    elif time_span_days >= 365 * 2:
        return 'medium_term_active'
    elif time_span_days >= 365:
        return 'established_updates'
    elif time_span_days >= 90:
        return 'recent_updates'
    elif time_span_days > 0:
        return 'early_stage'
    else:
        return 'unknown'


def _get_download_tier(download_count: int) -> str:
    """Get download popularity tier."""
    if download_count >= 10000000:
        return 'massive'
    elif download_count >= 1000000:
        return 'very_high'
    elif download_count >= 100000:
        return 'high'
    elif download_count >= 10000:
        return 'moderate'
    elif download_count >= 1000:
        return 'low'
    elif download_count > 0:
        return 'very_low'
    else:
        return 'none'


def _get_star_tier(stars: int) -> str:
    """Get star tier."""
    if stars >= 5000:
        return 'very_popular'
    elif stars >= 1000:
        return 'popular'
    elif stars >= 100:
        return 'moderate'
    elif stars >= 10:
        return 'low'
    elif stars > 0:
        return 'minimal'
    else:
        return 'none'


def _get_dependency_tier(dependency_count: int) -> str:
    """Get dependency complexity tier."""
    if dependency_count == 0:
        return 'standalone'
    elif dependency_count <= 5:
        return 'minimal'
    elif dependency_count <= 25:
        return 'moderate'
    elif dependency_count <= 100:
        return 'heavy'
    else:
        return 'very_heavy'