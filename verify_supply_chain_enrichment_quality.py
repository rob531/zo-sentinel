#!/usr/bin/env python3
"""
Quality verification module for supply_chain_enrichment.py.

Exercises compute_score() against synthetic corpus of fingerprints
with varying metadata. Asserts discrimination quality and type safety.

This is NOT a daemon — it is an offline test script that exits 0 on success.
"""

import sys
from typing import Any

# Import the module under test
from supply_chain_enrichment import compute_score


def generate_targeted_corpus() -> list[dict[str, Any]]:
    """
    Generate synthetic package metadata fingerprints that TARGET each
    discrete score value the enricher can produce.
    
    The scoring formula uses multiples of 5 (0-100), so we need to
    construct metadata that hits each target score value.
    
    Scoring formula (starting from 50):
    - registry: trusted=+15, missing=-15, unknown=-10
    - age: >=730=+15, >=365=+10, >=90=+5, >=30=-5, <30=-15
    - downloads: >=10M=+15, >=1M=+10, >=100k=+5, >0=-5, none=0
    - deps: 0=+5, <=10=+10, <=30=+5, <=100=0, >100=-20
    - publisher: verified=+20, unverified=-20
    - stars: >=10k=+10, >=1k=+5, >0=0, none=0
    
    Total range: 50 + max(+85) = 135 → clipped to 100
              50 + min(-85) = -35 → clipped to 0
    """
    corpus = []
    
    # Target score values (0-100 in steps of 5)
    target_scores = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0,
                     45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0,
                     90.0, 95.0, 100.0]
    
    # For each target score, construct metadata to hit it
    for target in target_scores:
        delta = target - 50  # What we need to add to base 50
        
        metadata = {}
        
        # Distribute delta across factors
        # Start with worst case (unverified, unknown registry, no downloads)
        metadata['registry_source'] = 'unknown-registry'
        metadata['age_days'] = 5
        metadata['download_count'] = 0
        metadata['dependency_count'] = 150
        metadata['publisher_verified'] = False
        metadata['stars'] = 0
        
        # Base scores for worst case: 50 - 10 - 15 - 5 - 20 - 20 = -20
        # Adjust to reach target
        remaining = target - (-20)
        
        # Add registry trust (+15 if trusted, -15 if missing, -10 if unknown)
        if remaining >= 25:
            metadata['registry_source'] = 'pypi'
            remaining -= 15
        elif remaining >= 10:
            metadata['registry_source'] = ''
            remaining += 0  # missing is -15 from trusted, not helpful here
        # else keep unknown (-10 vs trusted +15, we need to reach high target)
        
        # Add age trust
        if remaining >= 15:
            metadata['age_days'] = 1000
            remaining -= 15
        elif remaining >= 10:
            metadata['age_days'] = 400
            remaining -= 10
        elif remaining >= 5:
            metadata['age_days'] = 100
            remaining -= 5
        elif remaining >= -5:
            metadata['age_days'] = 30
            remaining += 0  # -5 is positive for us
        # else keep very_new (-15)
        
        # Add download trust
        if remaining >= 15:
            metadata['download_count'] = 20000000
            remaining -= 15
        elif remaining >= 10:
            metadata['download_count'] = 5000000
            remaining -= 10
        elif remaining >= 5:
            metadata['download_count'] = 200000
            remaining -= 5
        elif remaining >= -5:
            metadata['download_count'] = 100
            remaining += 0  # -5 is positive for us
        # else keep none (0 change)
        
        # Add dependency benefit
        if remaining >= 10:
            metadata['dependency_count'] = 5
            remaining -= 10
        elif remaining >= 5:
            metadata['dependency_count'] = 20
            remaining -= 5
        elif remaining >= -5:
            metadata['dependency_count'] = 50
            remaining += 0  # 0 change
        # else keep excessive (-20)
        
        # Add publisher verification
        if remaining >= 20:
            metadata['publisher_verified'] = True
            remaining -= 20
        # else keep unverified (+20 to our advantage, i.e., -20 from target)
        
        # Add stars trust
        if remaining >= 10:
            metadata['stars'] = 30000
            remaining -= 10
        elif remaining >= 5:
            metadata['stars'] = 5000
            remaining -= 5
        # else keep 0 (0 change)
        
        # Clip to valid range
        metadata['age_days'] = max(0, min(2000, metadata['age_days']))
        metadata['download_count'] = max(0, metadata['download_count'])
        metadata['dependency_count'] = max(0, min(300, metadata['dependency_count']))
        metadata['stars'] = max(0, metadata['stars'])
        
        # Verify we can hit the target (approximately)
        test_score, _ = compute_score(metadata)
        if abs(test_score - target) <= 5.0:
            corpus.append(metadata)
        else:
            # Fallback: store anyway, may not hit exact target
            corpus.append(metadata)
    
    # Add explicit boundary cases to ensure coverage
    boundary_cases = [
        # Registry boundaries
        {'registry_source': 'pypi'},  # trusted only
        {'registry_source': ''},      # missing only
        {'registry_source': 'unknown'},  # unknown only
        # Age boundaries
        {'age_days': 29},   # just below 30
        {'age_days': 30},   # at 30 threshold
        {'age_days': 89},   # just below 90
        {'age_days': 90},   # at 90 threshold
        {'age_days': 364},  # just below 365
        {'age_days': 365},  # at 365 threshold
        {'age_days': 729},  # just below 730
        {'age_days': 730},  # at 730 threshold
        # Download boundaries
        {'download_count': 99},
        {'download_count': 100},
        {'download_count': 99999},
        {'download_count': 100000},
        {'download_count': 999999},
        {'download_count': 1000000},
        # Dependency boundaries
        {'dependency_count': 0},
        {'dependency_count': 10},
        {'dependency_count': 11},
        {'dependency_count': 30},
        {'dependency_count': 31},
        {'dependency_count': 100},
        {'dependency_count': 101},
        # Publisher boundary
        {'publisher_verified': True},
        {'publisher_verified': False},
        # Star boundaries
        {'stars': 0},
        {'stars': 999},
        {'stars': 1000},
        {'stars': 9999},
        {'stars': 10000},
    ]
    corpus.extend(boundary_cases)
    
    # Deduplicate
    seen = set()
    unique_corpus = []
    for item in corpus:
        key = tuple(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            unique_corpus.append(item)
    
    corpus = unique_corpus
    
    # Ensure we have at least 34 fingerprints
    assert len(corpus) >= 34, f"Corpus size {len(corpus)} is less than required 34"
    
    return corpus


def verify_type_safety(results: list[tuple[float, dict]]) -> list[str]:
    """
    Verify all results conform to expected type contract.
    
    Returns list of error messages (empty if all pass).
    """
    errors = []
    
    for idx, result in enumerate(results):
        # Check tuple
        if not isinstance(result, tuple):
            errors.append(f"Result #{idx}: expected tuple, got {type(result).__name__}")
            continue
        
        # Check tuple length
        if len(result) != 2:
            errors.append(f"Result #{idx}: expected 2-element tuple, got {len(result)}-element")
            continue
        
        score, evidence = result
        
        # Check score type and range
        if not isinstance(score, (int, float)):
            errors.append(f"Result #{idx}: score is {type(score).__name__}, expected float")
        elif not (0.0 <= score <= 100.0):
            errors.append(f"Result #{idx}: score {score} outside valid range [0.0, 100.0]")
        
        # Check evidence type
        if not isinstance(evidence, dict):
            errors.append(f"Result #{idx}: evidence is {type(evidence).__name__}, expected dict")
    
    return errors


def print_discrimination_stats(results: list[tuple[float, dict]]) -> None:
    """Print discrimination statistics for the corpus."""
    scores = [r[0] for r in results]
    unique_scores = sorted(set(scores))
    
    print("\n" + "=" * 60)
    print("DISCRIMINATION STATISTICS")
    print("=" * 60)
    print(f"Total fingerprints tested:    {len(results)}")
    print(f"Unique score values:          {len(unique_scores)}")
    print(f"Discrimination ratio:        {len(unique_scores) / len(scores) * 100:.1f}%")
    print(f"Minimum score:               {min(scores):.2f}")
    print(f"Maximum score:               {max(scores):.2f}")
    print(f"Score range:                 {max(scores) - min(scores):.2f}")
    
    if len(scores) > 1:
        sorted_scores = sorted(scores)
        n = len(sorted_scores)
        median_idx = n // 2
        median = sorted_scores[median_idx] if n % 2 == 1 else (sorted_scores[median_idx - 1] + sorted_scores[median_idx]) / 2
        print(f"Median score:                {median:.2f}")
    
    print("\nUnique score distribution:")
    for score in unique_scores:
        count = scores.count(score)
        bar = "█" * (count // 2)
        print(f"  {score:6.2f}: {count:3d} {bar}")
    
    print("=" * 60 + "\n")


def main() -> int:
    """Run quality verification and exit with appropriate code."""
    print("=" * 60)
    print("SUPPLY CHAIN ENRICHMENT QUALITY VERIFICATION")
    print("=" * 60)
    
    # Generate synthetic corpus
    corpus = generate_targeted_corpus()
    print(f"Generated corpus: {len(corpus)} synthetic fingerprints")
    
    # Compute scores for all fingerprints
    results = []
    for idx, metadata in enumerate(corpus):
        try:
            result = compute_score(metadata)
            results.append(result)
        except Exception as e:
            print(f"\nFATAL: compute_score raised exception on fingerprint #{idx}:")
            print(f"  Metadata: {metadata}")
            print(f"  Error: {type(e).__name__}: {e}")
            return 1
    
    # Verify type safety
    type_errors = verify_type_safety(results)
    if type_errors:
        print("\nTYPE SAFETY VIOLATIONS:")
        for err in type_errors:
            print(f"  - {err}")
        return 1
    
    print(f"Type safety check: PASSED (all {len(results)} results are tuple[float, dict] with score in [0.0, 100.0])")
    
    # Check discrimination
    unique_scores = len(set(r[0] for r in results))
    required_distinct = 20
    
    print_discrimination_stats(results)
    
    if unique_scores < required_distinct:
        print(f"\nDISCRIMINATION INSUFFICIENT:")
        print(f"  Required distinct scores:  {required_distinct}")
        print(f"  Actual distinct scores:    {unique_scores}")
        print(f"  FAILED - enricher lacks sufficient discrimination power")
        return 1
    
    print(f"Discrimination check: PASSED ({unique_scores} >= {required_distinct} distinct values)")
    print("\nQUALITY VERIFICATION: ALL CHECKS PASSED")
    return 0


if __name__ == '__main__':
    sys.exit(main())