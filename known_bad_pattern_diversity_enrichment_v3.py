# deps: 
"""
Enrichment module for known_bad_pattern signal.
Implements compute_score(metadata) -> (float in [0,100], evidence dict).
Uses metadata fields: pattern_type, recurrence_count, severity_distribution,
 detection_source, age_days, false_positive_rate.
Evidence dict includes keys 'verdict' and 'missing' as required by contract.
"""

def compute_score(metadata: dict) -> tuple[float, dict]:
    """Compute a composite score (0-100) from multiple metadata fields.

    Args:
        metadata: dict containing any of the following keys:
            - pattern_type (str): e.g., 'malware', 'phishing', etc.
            - recurrence_count (int): how many times this pattern was seen.
            - severity_distribution (dict): mapping severity levels to counts,
              e.g., {'high': 3, 'medium': 5, 'low': 10}.
            - detection_source (str): source of detection, e.g., 'engine', 'user'.
            - age_days (int|float): age of the pattern in days.
            - false_positive_rate (float): 0.0-1.0 estimate.

    Returns:
        tuple: (score, evidence) where score is a float in [0,100] and evidence
        is a dict containing at least 'verdict' and 'missing' keys.
    """
    # Helper to safely extract values with defaults
    def get(key, default=None):
        return metadata.get(key, default)

    # Base score starts at 50 (midpoint) and will be adjusted.
    score = 50.0
    evidence = {
        "verdict": "unknown",
        "missing": []
    }

    # 1. pattern_type impact (higher risk for certain types)
    pattern_type = get('pattern_type')
    if pattern_type:
        risk_map = {
            'malware': 20,
            'ransomware': 25,
            'phishing': 15,
            'botnet': 18,
            'adware': 10,
        }
        adjustment = risk_map.get(str(pattern_type).lower(), 5)
        score += adjustment
    else:
        evidence['missing'].append('pattern_type')

    # 2. recurrence_count (more occurrences -> higher risk)
    rec = get('recurrence_count')
    if isinstance(rec, (int, float)):
        # Scale: each occurrence adds up to 0.5 points, capped at +20
        score += min(20, rec * 0.5)
    else:
        evidence['missing'].append('recurrence_count')

    # 3. severity_distribution (weighted severity score)
    sev_dist = get('severity_distribution')
    if isinstance(sev_dist, dict) and sev_dist:
        total = sum(sev_dist.values()) or 1
        high = sev_dist.get('high', 0)
        medium = sev_dist.get('medium', 0)
        low = sev_dist.get('low', 0)
        # Weighted severity: high=2, medium=1, low=0.5
        weighted = (2 * high + 1 * medium + 0.5 * low) / total
        # Map weighted severity (0-2) to score adjustment (0-15)
        score += weighted * 7.5
    else:
        evidence['missing'].append('severity_distribution')

    # 4. detection_source impact (trusted sources lower risk)
    source = get('detection_source')
    if source:
        source_risk = {
            'engine': -5,
            'user': 0,
            'third_party': 5,
            'unknown': 10,
        }
        score += source_risk.get(str(source).lower(), 5)
    else:
        evidence['missing'].append('detection_source')

    # 5. age_days (newer patterns higher risk)
    age = get('age_days')
    if isinstance(age, (int, float)) and age >= 0:
        if age < 1:
            score += 15
        elif age < 7:
            score += 10
        elif age < 30:
            score += 5
        # older patterns add no risk
    else:
        evidence['missing'].append('age_days')

    # 6. false_positive_rate (higher rate reduces confidence, lower risk)
    fpr = get('false_positive_rate')
    if isinstance(fpr, (int, float)):
        # Invert: low FPR -> higher risk, scale 0-20 points
        fpr = max(0.0, min(1.0, fpr))
        score += (1.0 - fpr) * 20
    else:
        evidence['missing'].append('false_positive_rate')

    # Clamp score to 0-100
    final_score = max(0.0, min(100.0, round(score, 2)))
    evidence['verdict'] = 'high' if final_score >= 70 else ('medium' if final_score >= 40 else 'low')
    # Ensure missing list is present even if empty
    if 'missing' not in evidence:
        evidence['missing'] = []
    return final_score, evidence

if __name__ == '__main__':
    # Self-test with synthetic metadata examples
    test_cases = [
        {
            'pattern_type': 'malware',
            'recurrence_count': 30,
            'severity_distribution': {'high': 5, 'medium': 10, 'low': 15},
            'detection_source': 'engine',
            'age_days': 0.5,
            'false_positive_rate': 0.05,
        },
        {
            'pattern_type': 'phishing',
            'recurrence_count': 5,
            'severity_distribution': {'high': 0, 'medium': 2, 'low': 8},
            'detection_source': 'user',
            'age_days': 45,
            'false_positive_rate': 0.2,
        },
        {
            # Missing several fields to test defaults
            'pattern_type': 'adware',
            'recurrence_count': 0,
            'severity_distribution': {},
            'detection_source': 'unknown',
            'age_days': None,
            'false_positive_rate': None,
        },
    ]
    for i, meta in enumerate(test_cases, 1):
        score, ev = compute_score(meta)
        assert 0.0 <= score <= 100.0, f"Score out of range: {score}"
        assert 'verdict' in ev, "Evidence missing 'verdict'"
        assert 'missing' in ev, "Evidence missing 'missing'"
        print(f"Test case {i}: score={score}, verdict={ev['verdict']}, missing={ev['missing']}")
    print("[PASS] All self-test assertions succeeded.")
