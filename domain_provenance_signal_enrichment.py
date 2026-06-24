"""
Domain provenance signal enrichment module.

Computes a weighted score based on domain age, registry source, and domain reputation.
"""

from datetime import datetime
from typing import Dict, Tuple, Any


def compute_score(metadata: dict) -> Tuple[float, Dict[str, Any]]:
    """
    Compute a weighted score based on domain provenance signals.
    
    Args:
        metadata: Dictionary containing domain, registry_source, and domain_registration_date
        
    Returns:
        Tuple of (score in 0..100, evidence dict with 'verdict' and 'missing' keys)
    """
    score = 0.0
    evidence = {
        'verdict': '',
        'missing': []
    }
    
    # Weight configuration
    WEIGHTS = {
        'domain_age': 0.4,
        'registry_source': 0.3,
        'domain_reputation': 0.3
    }
    
    # Process domain field
    if 'domain' in metadata and metadata['domain']:
        domain = metadata['domain']
        # Domain reputation contribution (based on domain characteristics)
        domain_parts = domain.split('.')
        tld = domain_parts[-1] if len(domain_parts) > 1 else ''
        
        # Known TLDs with higher trust
        known_tlds = {'com': 25, 'org': 24, 'net': 23, 'edu': 30, 'gov': 30}
        tld_score = known_tlds.get(tld.lower(), 15)
        
        # Domain length factor (too short or too long may be suspicious)
        sld_length = len(domain_parts[0]) if domain_parts else 0
        length_score = min(sld_length * 2, 10)
        
        domain_reputation = tld_score + length_score
        score += domain_reputation * WEIGHTS['domain_reputation']
        evidence['verdict'] = f"Domain {domain} analyzed"
    else:
        evidence['missing'].append('domain')
    
    # Process registry_source field
    if 'registry_source' in metadata and metadata['registry_source']:
        source = metadata['registry_source'].lower()
        # Known registries and their trust scores
        registry_scores = {
            'iana': 100,
            'icann': 95,
            'arin': 85,
            'ripe': 85,
            'apnic': 80,
            'lacnic': 75,
            'afrinic': 75
        }
        source_score = registry_scores.get(source, 50)  # Default 50 for unknown
        score += source_score * WEIGHTS['registry_source']
        if evidence['verdict']:
            evidence['verdict'] += f", Registry: {source}"
        else:
            evidence['verdict'] = f"Registry: {source}"
    else:
        evidence['missing'].append('registry_source')
    
    # Process domain_registration_date field
    if 'domain_registration_date' in metadata and metadata['domain_registration_date']:
        try:
            reg_date = datetime.strptime(metadata['domain_registration_date'], '%Y-%m-%d')
            now = datetime.now()
            age_days = (now - reg_date).days
            
            # Domain age scoring (older domains tend to be more legitimate)
            # Max out at ~10 years (3650 days)
            if age_days >= 0:
                age_score = min((age_days / 3650) * 100, 100)
            else:
                age_score = 0  # Future dates are suspicious
            
            score += age_score * WEIGHTS['domain_age']
            if evidence['verdict']:
                evidence['verdict'] += f", Age: {age_days} days"
            else:
                evidence['verdict'] = f"Age: {age_days} days"
        except ValueError:
            evidence['missing'].append('domain_registration_date')
    else:
        evidence['missing'].append('domain_registration_date')
    
    # Ensure score is in 0..100 range
    score = round(min(max(score, 0.0), 100.0), 2)
    
    return score, evidence


if __name__ == '__main__':
    # Self-test
    test_metadata = {
        'domain': 'example.com',
        'registry_source': 'iana',
        'domain_registration_date': '2020-01-01'
    }
    
    score, evidence = compute_score(test_metadata)
    
    assert 0 <= score <= 100, f"Score {score} out of range"
    assert 'verdict' in evidence, "Missing 'verdict' in evidence"
    assert 'missing' in evidence, "Missing 'missing' in evidence"
    
    print(f"Test metadata: {test_metadata}")
    print(f"Computed score: {score}")
    print(f"Evidence: {evidence}")
    print("PASS")