import subprocess
import requests
import sys

def run_population_script():
    try:
        # Run the population script
        subprocess.run([sys.executable, "populate_mcp_policy_rules_initial_set.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to run population script: {e}")
        sys.exit(1)

def verify_policy_rules():
    url = "http://127.0.0.1:8772/query"
    query = "SELECT COUNT(*) FROM mcp_policy_rules"
    headers = {"Content-Type": "application/json"}
    data = {"query": query}

    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        result = response.json()

        if result and result[0] and result[0][0] > 0:
            print("PASS")
        else:
            print("FAIL: No policy rules found in mcp_policy_rules table")
            sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Failed to query database: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_population_script()
    verify_policy_rules()