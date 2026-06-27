import requests
import time

def verify_attestation_refresher():
    """Verify that the attestation refresher is correctly generating and updating attestations."""
    # Define the URL for the attestation refresher
    url = "http://localhost:8000/refresh_attestation"
    
    # Define the expected outcome
    expected_outcome = {
        "status": "success",
        "message": "Attestation refreshed successfully"
    }
    
    # Send a POST request to the attestation refresher
    response = requests.post(url)
    
    # Check the response status code
    if response.status_code == 200:
        # Parse the response JSON
        response_json = response.json()
        
        # Verify the response matches the expected outcome
        if response_json == expected_outcome:
            print("Attestation refresher is working correctly.")
        else:
            print("Attestation refresher is not working as expected.")
    else:
        print(f"Failed to refresh attestation. Status code: {response.status_code}")

if __name__ == "__main__":
    verify_attestation_refresher()