def calculate_risk_tier(axis_scores: dict) -> tuple[str, float]:
    # Define axis weights and thresholds
    AXIS_WEIGHTS = {
        'domain_trust': 0.2,
        'capability': 0.2,
        'intent': 0.2,
        'autonomy': 0.2,
        'impact': 0.2
    }

    RISK_THRESHOLDS = {
        'LOW': 20,
        'MEDIUM': 40,
        'HIGH': 60,
        'CRITICAL': 80
    }

    # Check for critical override
    for axis, score in axis_scores.items():
        if score['label'] == 'critical' or score['p_top'] > 0.95:
            return ('HIGH_RISK_ISOLATED', 100.0)

    # Calculate weighted overall risk score
    overall_risk = 0.0
    for axis, score in axis_scores.items():
        if axis in AXIS_WEIGHTS:
            # Convert label to numerical score (0-100)
            if score['label'] == 'low':
                axis_score = 10
            elif score['label'] == 'medium':
                axis_score = 50
            elif score['label'] == 'high':
                axis_score = 80
            elif score['label'] == 'critical':
                axis_score = 100
            else:
                axis_score = 0

            overall_risk += axis_score * AXIS_WEIGHTS[axis]

    # Determine risk tier
    risk_tier = 'LOW'
    for tier, threshold in RISK_THRESHOLDS.items():
        if overall_risk >= threshold:
            risk_tier = tier

    return (risk_tier, overall_risk)

if __name__ == '__main__':
    # Test cases
    test_cases = [
        # No critical override
        (
            {
                'domain_trust': {'label': 'medium', 'p_top': 0.8},
                'capability': {'label': 'high', 'p_top': 0.85},
                'intent': {'label': 'medium', 'p_top': 0.7},
                'autonomy': {'label': 'low', 'p_top': 0.6},
                'impact': {'label': 'high', 'p_top': 0.9}
            },
            ('HIGH', 62.0)
        ),
        # With critical override
        (
            {
                'domain_trust': {'label': 'critical', 'p_top': 0.96},
                'capability': {'label': 'low', 'p_top': 0.5},
                'intent': {'label': 'low', 'p_top': 0.4},
                'autonomy': {'label': 'low', 'p_top': 0.3},
                'impact': {'label': 'low', 'p_top': 0.2}
            },
            ('HIGH_RISK_ISOLATED', 100.0)
        ),
        # All low scores
        (
            {
                'domain_trust': {'label': 'low', 'p_top': 0.1},
                'capability': {'label': 'low', 'p_top': 0.2},
                'intent': {'label': 'low', 'p_top': 0.3},
                'autonomy': {'label': 'low', 'p_top': 0.4},
                'impact': {'label': 'low', 'p_top': 0.5}
            },
            ('LOW', 10.0)
        ),
        # Mixed scores
        (
            {
                'domain_trust': {'label': 'medium', 'p_top': 0.6},
                'capability': {'label': 'high', 'p_top': 0.8},
                'intent': {'label': 'medium', 'p_top': 0.7},
                'autonomy': {'label': 'low', 'p_top': 0.5},
                'impact': {'label': 'medium', 'p_top': 0.65}
            },
            ('MEDIUM', 46.0)
        )
    ]

    # Run tests
    for i, (input_scores, expected) in enumerate(test_cases):
        result = calculate_risk_tier(input_scores)
        assert result == expected, f"Test case {i+1} failed: {result} != {expected}"
        print(f"Test case {i+1} PASS")

    print("All tests PASS")