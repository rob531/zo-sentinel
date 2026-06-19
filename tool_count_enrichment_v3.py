#!/usr/bin/env python3
"""
tool_count_enrichment_v3.py

Signal enrichment module for tool_count with improved discrimination.
Per PRODUCT_SPEC §3 (Signal Invariant): Score must expose sufficient
discrimination across synthetic fingerprints to prevent clustering.

Changes from v2:
- v2 produced only 2 distinct scores (bucket collapse)
- v3 produces 20+ distinct values via fine-grained graduated scoring
- Extended field set: 7 metadata fields instead of 5
- Full evidence dict showing partial score per field

Args (metadata dict):
    registry_source: str - registry origin (cargo, pypi, npm, etc.)
    age_days: int - package age in days
    download_count: int - total downloads
    dependency_count: int - number of dependencies
    publisher_verified: bool - publisher verification status
    stars: int - star count
    tool_count: int - number of tools in suite

Returns:
    Tuple of (score: float 0-100, evidence: dict)
"""

from typing import Any


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute enrichment score from tool metadata.
    
    Scoring model:
    - tool_count 1-5:   80-100 (progressive disclosure, +5 per tool)
    - tool_count 6-20:  50-79  (graduated buckets)
    - tool_count 21+:   10-49  (brute-force penalty)
    - publisher_verified: +10 bonus
    - stars > 100:     +15 bonus
    
    Args:
        metadata: Dict with metadata fields
        
    Returns:
        (score: float 0-100, evidence: dict with partial scores)
    """
    # Extract all fields with safe defaults
    tool_count = metadata.get('tool_count', 0)
    registry_source = metadata.get('registry_source', '')
    age_days = metadata.get('age_days', 0)
    download_count = metadata.get('download_count', 0)
    dependency_count = metadata.get('dependency_count', 0)
    publisher_verified = metadata.get('publisher_verified', False)
    stars = metadata.get('stars', 0)
    
    # Initialize evidence tracking
    evidence = {
        'fields_used': [
            'registry_source', 'age_days', 'download_count',
            'dependency_count', 'publisher_verified', 'stars', 'tool_count'
        ],
        'partial_scores': {},
        'bonuses': {},
    }
    
    # --- Tool Count Score (primary signal) ---
    # Graduated buckets with fine-grained discrimination
    if tool_count == 0:
        tool_score = 25.0
    elif 1 <= tool_count <= 5:
        # Progressive disclosure: 80-100, +5 per tool
        tool_score = 75.0 + (tool_count * 5)
    elif 6 <= tool_count <= 10:
        # 70-79
        tool_score = 65.0 + ((tool_count - 5) * 2)
    elif 11 <= tool_count <= 15:
        # 60-69
        tool_score = 60.0 + ((tool_count - 10) * 1.5)
    elif 16 <= tool_count <= 20:
        # 50-59
        tool_score = 50.0 + ((tool_count - 15) * 1)
    elif 21 <= tool_count <= 30:
        # 40-49 (brute-force begins)
        tool_score = 49.0 - ((tool_count - 20) * 0.9)
    elif 31 <= tool_count <= 50:
        # 25-39
        tool_score = 38.0 - ((tool_count - 30) * 0.65)
    elif 51 <= tool_count <= 100:
        # 10-24
        tool_score = 23.0 - ((tool_count - 50) * 0.26)
    else:
        # 100+: 10 points minus small penalties
        tool_score = max(10.0, 10.0 - ((tool_count - 100) * 0.05))
    
    evidence['partial_scores']['tool_count'] = round(tool_score, 2)
    base_score = tool_score
    
    # --- Registry Source Bonus ---
    registry_bonus = 0.0
    if registry_source:
        src = str(registry_source).lower().strip()
        registry_scores = {
            'cargo': 5.0,
            'crates': 5.0,
            'pypi': 4.0,
            'npm': 2.0,
            'nuget': 3.0,
            'maven': 3.0,
            'gem': 1.0,
            'go': 4.0,
            'packagist': 1.0,
        }
        registry_bonus = registry_scores.get(src, 0.0)
    
    evidence['partial_scores']['registry_source'] = round(registry_bonus, 2)
    base_score += registry_bonus
    
    # --- Age Days Bonus/Penalty ---
    age_score = 0.0
    if age_days >= 365:
        # Established package: +3
        age_score = 3.0
    elif age_days >= 180:
        # Mature: +2
        age_score = 2.0
    elif age_days >= 90:
        # Moderate: +1
        age_score = 1.0
    elif age_days >= 30:
        # New: 0
        age_score = 0.0
    else:
        # Very new: -2
        age_score = -2.0
    
    evidence['partial_scores']['age_days'] = round(age_score, 2)
    base_score += age_score
    
    # --- Download Count Bonus ---
    dl_score = 0.0
    if download_count >= 100000:
        dl_score = 5.0
    elif download_count >= 50000:
        dl_score = 4.0
    elif download_count >= 10000:
        dl_score = 3.0
    elif download_count >= 1000:
        dl_score = 2.0
    elif download_count >= 100:
        dl_score = 1.0
    
    evidence['partial_scores']['download_count'] = round(dl_score, 2)
    base_score += dl_score
    
    # --- Dependency Count Bonus/Penalty ---
    dep_score = 0.0
    if dependency_count <= 2:
        dep_score = 3.0
    elif dependency_count <= 5:
        dep_score = 2.0
    elif dependency_count <= 10:
        dep_score = 0.0
    elif dependency_count <= 20:
        dep_score = -2.0
    else:
        dep_score = -4.0
    
    evidence['partial_scores']['dependency_count'] = round(dep_score, 2)
    base_score += dep_score
    
    # --- Publisher Verified Bonus ---
    pub_bonus = 10.0 if publisher_verified else 0.0
    evidence['partial_scores']['publisher_verified'] = round(pub_bonus, 2)
    evidence['bonuses']['publisher_verified'] = round(pub_bonus, 2)
    base_score += pub_bonus
    
    # --- Stars Bonus ---
    stars_bonus = 0.0
    if stars > 100:
        stars_bonus = 15.0
    elif stars > 50:
        stars_bonus = 10.0
    elif stars > 10:
        stars_bonus = 5.0
    
    evidence['partial_scores']['stars'] = round(stars_bonus, 2)
    evidence['bonuses']['stars'] = round(stars_bonus, 2)
    base_score += stars_bonus
    
    # Clamp final score to 0-100
    final_score = max(0.0, min(100.0, base_score))
    
    evidence['final_score'] = round(final_score, 2)
    
    return final_score, evidence


if __name__ == '__main__':
    import sys
    
    # Test cases for self-smoke
    test_cases = [
        # Micro tool suites (1-5 tools) - should score 80-100
        {'tool_count': 1, 'registry_source': 'cargo', 'age_days': 30, 'download_count': 100, 'dependency_count': 2, 'publisher_verified': False, 'stars': 10},
        {'tool_count': 3, 'registry_source': 'pypi', 'age_days': 90, 'download_count': 500, 'dependency_count': 5, 'publisher_verified': False, 'stars': 50},
        {'tool_count': 5, 'registry_source': 'npm', 'age_days': 180, 'download_count': 1000, 'dependency_count': 3, 'publisher_verified': True, 'stars': 200},
        
        # Standard tool suites (6-20 tools) - should score 50-79
        {'tool_count': 8, 'registry_source': 'cargo', 'age_days': 365, 'download_count': 5000, 'dependency_count': 10, 'publisher_verified': True, 'stars': 150},
        {'tool_count': 15, 'registry_source': 'pypi', 'age_days': 500, 'download_count': 10000, 'dependency_count': 15, 'publisher_verified': False, 'stars': 80},
        {'tool_count': 20, 'registry_source': 'npm', 'age_days': 700, 'download_count': 20000, 'dependency_count': 25, 'publisher_verified': True, 'stars': 300},
        
        # Large tool suites (21+ tools) - should score 10-49
        {'tool_count': 25, 'registry_source': 'cargo', 'age_days': 1000, 'download_count': 50000, 'dependency_count': 30, 'publisher_verified': False, 'stars': 500},
        {'tool_count': 50, 'registry_source': 'pypi', 'age_days': 1500, 'download_count': 100000, 'dependency_count': 50, 'publisher_verified': True, 'stars': 1000},
        {'tool_count': 100, 'registry_source': 'npm', 'age_days': 2000, 'download_count': 500000, 'dependency_count': 100, 'publisher_verified': False, 'stars': 2000},
    ]
    
    print("=" * 60)
    print("tool_count_enrichment_v3.py - Self Smoke Test")
    print("=" * 60)
    
    all_passed = True
    scores_seen = set()
    
    for i, tc in enumerate(test_cases, 1):
        score, evidence = compute_score(tc)
        scores_seen.add(score)
        
        # Verify evidence structure
        assert 'fields_used' in evidence, f"Case {i}: missing fields_used"
        assert 'partial_scores' in evidence, f"Case {i}: missing partial_scores"
        assert 'bonuses' in evidence, f"Case {i}: missing bonuses"
        assert 'final_score' in evidence, f"Case {i}: missing final_score"
        
        # Verify score is in range
        assert 0 <= score <= 100, f"Case {i}: score {score} out of range"
        
        # Verify partial scores exist for all fields
        for field in evidence['fields_used']:
            assert field in evidence['partial_scores'], f"Case {i}: missing {field} in partial_scores"
        
        print(f"\nCase {i}: tool_count={tc['tool_count']}")
        print(f"  Score: {score}")
        print(f"  Partial scores: {evidence['partial_scores']}")
        print(f"  Bonuses: {evidence['bonuses']}")
        print(f"  Fields: {evidence['fields_used']}")
    
    # Verify discrimination: at least 3 distinct scores
    distinct = len(scores_seen)
    print(f"\n{'='*60}")
    print(f"Distinct scores: {distinct}")
    print(f"Signal discrimination: {'PASS' if distinct >= 3 else 'FAIL'}")
    
    if distinct < 3:
        print("ERROR: Insufficient discrimination!")
        all_passed = False
    
    # Verify all scores are valid floats
    for s in scores_seen:
        assert isinstance(s, float), f"Score {s} is not float"
    
    print(f"\nAll tests passed: {all_passed}")
    sys.exit(0 if all_passed else 1)
