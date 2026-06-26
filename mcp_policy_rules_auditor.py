import requests

def audit_server_policies(server_id: str) -> dict:
    # Retrieve active mcp_policy_rules
    rules_response = requests.get('http://write_service/mcp_policy_rules')
    rules_response.raise_for_status()
    rules = rules_response.json()

    # Retrieve server data
    server_response = requests.get(f'http://write_service/mcp_server_registry/{server_id}')
    server_response.raise_for_status()
    server_data = server_response.json()

    audit_result = {}

    for rule in rules:
        rule_name = rule['name']
        rule_condition = rule['condition']
        rule_evidence = rule['evidence']

        # Evaluate server against rule condition
        try:
            passed = eval(rule_condition, {'server': server_data})
        except Exception as e:
            passed = False
            evidence = f"Error evaluating rule: {str(e)}"
        else:
            evidence = rule_evidence.format(server=server_data) if rule_evidence else None

        audit_result[rule_name] = {
            'status': 'passed' if passed else 'failed',
            'evidence': evidence
        }

    return audit_result

if __name__ == '__main__':
    test_server_id = 'test_server_123'
    audit_result = audit_server_policies(test_server_id)

    # Assert a valid audit result
    assert isinstance(audit_result, dict)
    assert all(isinstance(v, dict) and 'status' in v for v in audit_result.values())

    print('PASS')