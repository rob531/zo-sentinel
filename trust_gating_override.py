OFFICIAL_ORG_ALLOWLIST = {
    'microsoft',
    'google',
    'googleapis',
    'stripe',
    'supabase',
    'cloudflare',
    'docker',
    'anthropic',
    'aws'
}

def trust_gating_override(labels, registry_source, url):
    """
    Deterministic verdict override following SSL-Labs criticalFailure/override pattern.
    When maintainer_trust=ESTABLISHED OR registry_source/url matches OFFICIAL_ORG_ALLOWLIST,
    CAP overall_risk so it NEVER exceeds MEDIUM and reframe as 'high-capability, trusted'.

    Args:
        labels: dict containing 6-axis labels including maintainer_trust and overall_risk
        registry_source: str indicating the registry source
        url: str containing the URL to check against allowlist

    Returns:
        tuple: (adjusted tier string, reason string)
    """
    reason = ""
    original_tier = labels.get('overall_risk', 'HIGH')

    # Check if maintainer is established or org is in allowlist
    if labels.get('maintainer_trust') == 'ESTABLISHED':
        reason = "Maintainer trust established - capping risk to MEDIUM"
        return ('MEDIUM', reason)
    elif any(org in url.lower() for org in OFFICIAL_ORG_ALLOWLIST):
        reason = f"URL matches official org allowlist - capping risk to MEDIUM"
        return ('MEDIUM', reason)

    # If none of the above, return original tier with no reason
    return (original_tier, reason)

def test_trust_gating_override():
    # Test case 1: ESTABLISHED maintainer with critical data
    labels1 = {
        'maintainer_trust': 'ESTABLISHED',
        'capability_breadth': 'BROAD',
        'data_sensitivity': 'CRITICAL',
        'overall_risk': 'HIGH'
    }
    tier1, reason1 = trust_gating_override(labels1, 'dockerhub', 'https://github.com/microsoft/container')
    assert tier1 == 'MEDIUM', f"Test 1 failed: Expected MEDIUM, got {tier1}"
    assert "Maintainer trust established" in reason1, f"Test 1 failed: Reason mismatch - {reason1}"

    # Test case 2: UNKNOWN maintainer with broad capability
    labels2 = {
        'maintainer_trust': 'UNKNOWN',
        'capability_breadth': 'BROAD',
        'data_sensitivity': 'LOW',
        'overall_risk': 'HIGH'
    }
    tier2, reason2 = trust_gating_override(labels2, 'dockerhub', 'https://github.com/unknown/container')
    assert tier2 == 'HIGH', f"Test 2 failed: Expected HIGH, got {tier2}"
    assert reason2 == "", f"Test 2 failed: Expected empty reason, got {reason2}"

    # Test case 3: URL in allowlist
    labels3 = {
        'maintainer_trust': 'UNKNOWN',
        'capability_breadth': 'NARROW',
        'data_sensitivity': 'MEDIUM',
        'overall_risk': 'HIGH'
    }
    tier3, reason3 = trust_gating_override(labels3, 'dockerhub', 'https://github.com/googleapis/container')
    assert tier3 == 'MEDIUM', f"Test 3 failed: Expected MEDIUM, got {tier3}"
    assert "URL matches official org allowlist" in reason3, f"Test 3 failed: Reason mismatch - {reason3}"

    print("PASS")

if __name__ == "__main__":
    test_trust_gating_override()