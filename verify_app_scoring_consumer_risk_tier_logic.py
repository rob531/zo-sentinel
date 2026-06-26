import app_scoring_consumer

def verify_risk_tier_logic(test_data: list[dict]) -> dict:
    results = {'success': True, 'details': []}

    for test_case in test_data:
        mcp_llm_axis_scores = test_case['mcp_llm_axis_scores']
        expected_risk_tier = test_case['expected_risk_tier']

        computed_risk_tier = app_scoring_consumer.translate_mcp_llm_axis_scores_to_risk_tier(mcp_llm_axis_scores)

        if computed_risk_tier != expected_risk_tier:
            results['success'] = False
            results['details'].append({
                'mcp_llm_axis_scores': mcp_llm_axis_scores,
                'expected_risk_tier': expected_risk_tier,
                'computed_risk_tier': computed_risk_tier
            })

    return results

if __name__ == '__main__':
    test_data = [
        {
            'mcp_llm_axis_scores': {
                'domain_trust': 0.9,
                'tool_description_safety': 0.8,
                'tool_usage_safety': 0.7,
                'tool_privacy_safety': 0.6,
                'tool_accuracy_safety': 0.5
            },
            'expected_risk_tier': 'low'
        },
        {
            'mcp_llm_axis_scores': {
                'domain_trust': 0.6,
                'tool_description_safety': 0.7,
                'tool_usage_safety': 0.8,
                'tool_privacy_safety': 0.9,
                'tool_accuracy_safety': 0.8
            },
            'expected_risk_tier': 'medium'
        },
        {
            'mcp_llm_axis_scores': {
                'domain_trust': 0.3,
                'tool_description_safety': 0.4,
                'tool_usage_safety': 0.5,
                'tool_privacy_safety': 0.6,
                'tool_accuracy_safety': 0.7
            },
            'expected_risk_tier': 'high'
        }
    ]

    results = verify_risk_tier_logic(test_data)

    if results['success']:
        print('PASS')
    else:
        print('FAIL')
        for detail in results['details']:
            print(f"Discrepancy found: {detail}")