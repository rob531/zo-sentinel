import requests

def check_mcp_policy_rules_population():
    # Define the query to check if mcp_policy_rules is populated
    query = "SELECT COUNT(*) FROM mcp_policy_rules"

    # Define the endpoint for the write_service
    write_service_url = "http://write_service:5000/query"

    try:
        # Send the query to the write_service
        response = requests.post(write_service_url, json={'query': query})
        response.raise_for_status()

        # Parse the response
        result = response.json()
        count = result['result'][0][0]

        # Check if the table is populated
        if count > 0:
            print("Status: mcp_policy_rules is populated with {} entries.".format(count))
        else:
            print("Status: mcp_policy_rules is empty. No policy rules are currently active.")
            print("Suggestion: Use populate_mcp_policy_rules_initial_set.py to populate the table with initial policy rules.")

    except requests.exceptions.RequestException as e:
        print("Error: Failed to query mcp_policy_rules. Details: {}".format(e))

if __name__ == "__main__":
    check_mcp_policy_rules_population()