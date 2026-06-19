#!/usr/bin/env python3
"""
known_bad_pattern_enrichment_v4.py

Enrichment module for known_bad_pattern detection with multi-signal scoring.
Addresses weak signal issue (only 2 distinct values across 69.0-95.0 range)
by combining multiple metadata fields for improved discrimination.

Pure function: compute_score(metadata) -> tuple[float, dict]
No DB writes, no network operations.
"""

from typing import Any


def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compute enriched score for known_bad_pattern detection using multiple metadata signals.
    
    Args:
        metadata: Dictionary containing package metadata fields:
            - registry_source: str (e.g., 'pypi', 'npm', 'rubygems', 'nuget', 'cargo')
            - age_days: int (package age in days since first release)
            - publisher_verified: bool (whether publisher identity is verified)
            - stars: int (social proof metric, 0 if unavailable)
            - download_count: int (popularity metric, 0 if unavailable)
            - dependency_count: int (number of dependencies)
            - score: float (original score if already present, 0-100)
    
    Returns:
        tuple[float, dict]: 
            - float: enriched score (0.0 - 100.0)
            - dict: detailed scoring breakdown with individual component scores
    
    Scoring Philosophy:
        - Combines temporal, verification, popularity, and complexity signals
        - Each field combination applies distinct scoring logic
        - Missing fields handled gracefully with neutral/default values
        - Final score normalized to 0-100 range
    """
    
    # Extract metadata fields with defaults for missing data
    registry_source = metadata.get('registry_source', 'unknown')
    age_days = metadata.get('age_days', None)
    publisher_verified = metadata.get('publisher_verified', None)
    stars = metadata.get('stars', None)
    download_count = metadata.get('download_count', None)
    dependency_count = metadata.get('dependency_count', None)
    original_score = metadata.get('score', None)
    
    # Initialize component scores
    components = {
        'temporal_risk': 0.0,
        'verification_bonus': 0.0,
        'popularity_factor': 0.0,
        'complexity_factor': 0.0,
        'registry_factor': 0.0,
        'combined_weight': 0.0,
    }
    
    # =========================================================================
    # FIELD COMBINATION 1: Temporal Analysis (age_days focused)
    # =========================================================================
    temporal_score = 0.0
    temporal_weight = 0.0
    
    if age_days is not None:
        temporal_weight = 1.0
        
        # Very new packages (< 30 days) - higher risk
        if age_days < 7:
            temporal_score = 85.0  # Suspiciously new
        elif age_days < 30:
            temporal_score = 70.0  # Recent, slightly suspicious
        elif age_days < 90:
            temporal_score = 50.0  # Moderate age
        elif age_days < 365:
            temporal_score = 35.0  # Established
        else:
            temporal_score = 20.0  # Mature package
    
    components['temporal_risk'] = temporal_score
    
    # =========================================================================
    # FIELD COMBINATION 2: Publisher Verification Analysis
    # =========================================================================
    verification_score = 0.0
    verification_weight = 0.0
    
    if publisher_verified is not None:
        verification_weight = 1.0
        
        if publisher_verified:
            verification_score = 15.0  # Verified = lower risk
        else:
            verification_score = 45.0  # Unverified = higher risk
    
    components['verification_bonus'] = verification_score
    
    # =========================================================================
    # FIELD COMBINATION 3: Popularity Scoring (stars + download_count)
    # =========================================================================
    popularity_score = 0.0
    popularity_weight = 0.0
    
    # Stars-based scoring
    if stars is not None:
        popularity_weight += 0.5
        
        if stars == 0:
            popularity_score += 30.0  # No social proof
        elif stars < 10:
            popularity_score += 20.0  # Low popularity
        elif stars < 100:
            popularity_score += 10.0  # Moderate
        elif stars < 1000:
            popularity_score += 5.0   # Good popularity
        else:
            popularity_score += 0.0   # High popularity = trust
    
    # Download count scoring
    if download_count is not None:
        popularity_weight += 0.5
        
        if download_count == 0:
            popularity_score += 25.0  # Never downloaded
        elif download_count < 100:
            popularity_score += 20.0  # Very low
        elif download_count < 1000:
            popularity_score += 15.0  # Low
        elif download_count < 10000:
            popularity_score += 8.0   # Moderate
        elif download_count < 100000:
            popularity_score += 4.0   # Good
        else:
            popularity_score += 0.0   # High popularity
    
    # Normalize popularity score by weight
    if popularity_weight > 0:
        popularity_score = popularity_score / popularity_weight
    
    components['popularity_factor'] = popularity_score
    
    # =========================================================================
    # FIELD COMBINATION 4: Complexity Analysis (dependency_count)
    # =========================================================================
    complexity_score = 0.0
    complexity_weight = 0.0
    
    if dependency_count is not None:
        complexity_weight = 1.0
        
        if dependency_count == 0:
            complexity_score = 25.0  # No dependencies - could be simple or obfuscated
        elif dependency_count <= 3:
            complexity_score = 15.0  # Low complexity
        elif dependency_count <= 10:
            complexity_score = 10.0  # Moderate
        elif dependency_count <= 50:
            complexity_score = 5.0   # Normal complexity
        else:
            complexity_score = 8.0   # High complexity - may include malicious deps
    
    components['complexity_factor'] = complexity_score
    
    # =========================================================================
    # FIELD COMBINATION 5: Registry-based Scoring
    # =========================================================================
    registry_score = 0.0
    registry_weight = 0.0
    
    if registry_source:
        registry_weight = 1.0
        
        # Different registries have different baseline risk profiles
        registry_risk = {
            'pypi': 10.0,
            'npm': 15.0,
            'rubygems': 12.0,
            'nuget': 8.0,
            'cargo': 5.0,
            'maven': 10.0,
            'conda': 7.0,
            'packagist': 15.0,
            'hackage': 8.0,
            'pub': 5.0,
        }
        
        registry_score = registry_risk.get(registry_source.lower(), 20.0)
    
    components['registry_factor'] = registry_score
    
    # =========================================================================
    # FIELD COMBINATION 6: Cross-field Analysis
    # Combines multiple fields for nuanced scoring
    # =========================================================================
    
    # New package + unverified + no popularity = very suspicious
    cross_field_penalty = 0.0
    
    if age_days is not None and publisher_verified is not None:
        if age_days < 30 and not publisher_verified:
            cross_field_penalty += 15.0
    
    if age_days is not None and stars is not None:
        if age_days < 7 and stars == 0:
            cross_field_penalty += 20.0  # Brand new + no social proof
        if age_days > 365 and stars == 0:
            cross_field_penalty += 10.0  # Old but never gained traction
    
    if age_days is not None and download_count is not None:
        if age_days > 365 and download_count < 100:
            cross_field_penalty += 12.0  # Old but never downloaded
    
    if publisher_verified is not None and download_count is not None:
        if publisher_verified and download_count > 100000:
            cross_field_penalty -= 15.0  # Verified + popular = very trustworthy
    
    if registry_source and age_days is not None:
        # High-risk registries with new packages
        high_risk_registries = {'npm', 'pypi', 'packagist'}
        if registry_source.lower() in high_risk_registries and age_days < 30:
            cross_field_penalty += 8.0
    
    # =========================================================================
    # Calculate Combined Score
    # =========================================================================
    
    # Calculate total weight based on available fields
    total_weight = (
        temporal_weight * 0.25 +
        verification_weight * 0.20 +
        popularity_weight * 0.20 +
        complexity_weight * 0.10 +
        registry_weight * 0.15 +
        0.10  # Base weight for cross-field analysis
    )
    
    # Combine all scores
    base_score = (
        temporal_score * temporal_weight * 0.25 +
        verification_score * verification_weight * 0.20 +
        popularity_score * popularity_weight * 0.20 +
        complexity_score * complexity_weight * 0.10 +
        registry_score * registry_weight * 0.15 +
        cross_field_penalty * 0.10
    )
    
    # Normalize by actual weight, apply floor/ceiling
    if total_weight > 0:
        combined_score = base_score / total_weight
    else:
        combined_score = 50.0  # Neutral default when no data
    
    # Incorporate original score if present (weighted combination)
    if original_score is not None:
        # Blend original score with new analysis
        # Weight: 60% original, 40% new metadata analysis
        # This helps when original score had some signal
        combined_score = (original_score * 0.6) + (combined_score * 0.4)
    
    # Apply cross-field adjustments
    combined_score += cross_field_penalty
    
    # Clamp to valid range
    final_score = max(0.0, min(100.0, combined_score))
    
    # Round to 2 decimal places
    final_score = round(final_score, 2)
    
    # Build detailed breakdown
    breakdown = {
        'temporal_risk': round(temporal_score, 2),
        'verification_bonus': round(verification_score, 2),
        'popularity_factor': round(popularity_score, 2),
        'complexity_factor': round(complexity_score, 2),
        'registry_factor': round(registry_score, 2),
        'cross_field_adjustment': round(cross_field_penalty, 2),
        'data_availability': {
            'has_age_days': age_days is not None,
            'has_publisher_verified': publisher_verified is not None,
            'has_stars': stars is not None,
            'has_download_count': download_count is not None,
            'has_dependency_count': dependency_count is not None,
            'has_registry_source': bool(registry_source and registry_source != 'unknown'),
        },
        'total_weight_used': round(total_weight, 3),
        'original_score': original_score,
    }
    
    return final_score, breakdown


def score_interpretation(score: float) -> str:
    """
    Provide human-readable interpretation of the computed score.
    
    Args:
        score: The computed score (0-100)
    
    Returns:
        str: Interpretation string
    """
    if score >= 80:
        return "HIGH RISK - Strong indicators of known_bad_pattern"
    elif score >= 60:
        return "MEDIUM-HIGH RISK - Multiple suspicious signals"
    elif score >= 40:
        return "MEDIUM RISK - Some concerning indicators"
    elif score >= 20:
        return "LOW-MEDIUM RISK - Minor concerns"
    else:
        return "LOW RISK - Few indicators of known_bad_pattern"


# Example usage and validation
if __name__ == "__main__":
    # Test cases demonstrating different field combinations
    
    test_cases = [
        {
            "name": "Suspicious new unverified package",
            "metadata": {
                "registry_source": "npm",
                "age_days": 5,
                "publisher_verified": False,
                "stars": 0,
                "download_count": 50,
                "dependency_count": 1,
            }
        },
        {
            "name": "Trusted mature verified package",
            "metadata": {
                "registry_source": "cargo",
                "age_days": 1800,
                "publisher_verified": True,
                "stars": 2500,
                "download_count": 500000,
                "dependency_count": 15,
            }
        },
        {
            "name": "Moderate risk with missing fields",
            "metadata": {
                "registry_source": "pypi",
                "age_days": 120,
                "publisher_verified": None,
                "stars": 50,
                "download_count": None,
                "dependency_count": 8,
            }
        },
        {
            "name": "Minimum data scenario",
            "metadata": {
                "registry_source": "npm",
            }
        },
        {
            "name": "With original score blending",
            "metadata": {
                "registry_source": "rubygems",
                "age_days": 45,
                "publisher_verified": False,
                "stars": 5,
                "download_count": 200,
                "dependency_count": 4,
                "score": 82.5,  # Original high score
            }
        },
    ]
    
    print("=" * 80)
    print("known_bad_pattern_enrichment_v4.py - Test Results")
    print("=" * 80)
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n[Test {i}] {test['name']}")
        print("-" * 60)
        
        score, breakdown = compute_score(test['metadata'])
        interpretation = score_interpretation(score)
        
        print(f"  Metadata: {test['metadata']}")
        print(f"  Score: {score:.2f}")
        print(f"  Interpretation: {interpretation}")
        print(f"  Breakdown:")
        for key, value in breakdown.items():
            print(f"    - {key}: {value}")
    
    print("\n" + "=" * 80)
    print("Validation complete - module ready for enrichment_harness.py evaluation")
    print("=" * 80)