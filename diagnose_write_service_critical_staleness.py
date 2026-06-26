import requests

def diagnose_write_service():
    try:
        # Attempt to connect to the write_service health endpoint
        response = requests.get('http://127.0.0.1:8772/query', params={'q': 'SELECT 1'})
        if response.status_code == 200:
            print("PASS: write_service is responsive.")
        else:
            print(f"FAIL: write_service returned status code {response.status_code}.")
    except requests.exceptions.RequestException as e:
        print(f"FAIL: Could not connect to write_service. Error: {e}")

if __name__ == '__main__':
    diagnose_write_service()