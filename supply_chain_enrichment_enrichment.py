"""Supply Chain Enrichment enrichment module."""


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute supply chain enrichment score from metadata.
    
    Args:
        metadata: dict containing 'supply_chain_enrichment' key with value like
                  'none', 'low', 'medium', 'high', or 'critical'
    
    Returns:
        tuple: (score in 0-100 range, evidence dict with 'verdict' and 'missing' keys)
    """
    evidence = {
        'verdict': None,
        'missing': []
    }
    
    # Weighted formula for supply chain enrichment levels
    # Weights sum to 1.0: 0.0 + 0.2 + 0.25 + 0.25 + 0.3 = 1.0
    enrichment_weights = {
        'none': 0.0,
        'low': 0.2,
        'medium': 0.25,
        'high': 0.25,
        'critical': 0.3
    }
    
    supply_chain_enrichment = metadata.get('supply_chain_enrichment')
    
    if supply_chain_enrichment is None:
        evidence['missing'].append('supply_chain_enrichment')
        score = 0
    else:
        weight = enrichment_weights.get(supply_chain_enrichment.lower(), 0.0)
        score = weight * 100
    
    evidence['verdict'] = 'inadequate' if score < 50 else 'adequate'
    
    return score, evidence


if __name__ == '__main__':
    score, evidence = compute_score({'supply_chain_enrichment': 'high'})
    assert 0 <= score <= 100, f"Score {score} out of range"
    assert 'verdict' in evidence, "Missing 'verdict' in evidence"
    print("PASS")