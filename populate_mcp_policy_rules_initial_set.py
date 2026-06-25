import requests
import json

# Define the base URL for the management API
BASE_URL = "http://localhost:8780"

# Define the default policy rules
DEFAULT_POLICY_RULES = [
    {
        "rule_name": "deny_high_risk",
        "rule_description": "Deny if overall_risk > 80",
        "rule_condition": "overall_risk > 80",
        "rule_action": "deny",
        "rule_priority": 1
    },
    {
        "rule_name": "warn_low_supply_chain_score",
        "rule_description": "Warn if supply_chain_score < 20",
        "rule_condition": "supply_chain_score < 20",
        "rule_action": "warn",
        "rule_priority": 2
    }
]

def check_rule_exists(rule_name):
    """Check if a rule with the given name already exists."""
    response = requests.get(f"{BASE_URL}/policy_rules")
    if response.status_code == 200:
        rules = response.json()
        for rule in rules:
            if rule["rule_name"] == rule_name:
                return True
    return False

def populate_default_policy_rules():
    """Populate the mcp_policy_rules table with default policy rules."""
    for rule in DEFAULT_POLICY_RULES:
        if not check_rule_exists(rule["rule_name"]):
            response = requests.post(f"{BASE_URL}/policy_rules", json=rule)
            if response.status_code != 201:
                print(f"Failed to insert rule {rule['rule_name']}: {response.text}")

def test_populate_default_policy_rules():
    """Test that the default policy rules are correctly populated."""
    # Populate the default policy rules
    populate_default_policy_rules()

    # Retrieve the policy rules from the API
    response = requests.get(f"{BASE_URL}/policy_rules")
    assert response.status_code == 200, f"Failed to retrieve policy rules: {response.text}"

    rules = response.json()
    assert len(rules) == len(DEFAULT_POLICY_RULES), f"Expected {len(DEFAULT_POLICY_RULES)} rules, got {len(rules)}"

    for rule in DEFAULT_POLICY_RULES:
        found = False
        for retrieved_rule in rules:
            if retrieved_rule["rule_name"] == rule["rule_name"]:
                found = True
                assert retrieved_rule["rule_description"] == rule["rule_description"], f"Description mismatch for rule {rule['rule_name']}"
                assert retrieved_rule["rule_condition"] == rule["rule_condition"], f"Condition mismatch for rule {rule['rule_name']}"
                assert retrieved_rule["rule_action"] == rule["rule_action"], f"Action mismatch for rule {rule['rule_name']}"
                assert retrieved_rule["rule_priority"] == rule["rule_priority"], f"Priority mismatch for rule {rule['rule_name']}"
                break
        assert found, f"Rule {rule['rule_name']} not found"

if __name__ == '__main__':
    test_populate_default_policy_rules()
    print("All tests passed!")