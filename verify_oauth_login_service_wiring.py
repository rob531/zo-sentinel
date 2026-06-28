from fastapi.testclient import TestClient
from main import app  # Assuming your FastAPI app is in main.py

client = TestClient(app)

def test_oauth_login_service_wiring():
    response = client.get("/oauth/login")
    assert response.status_code == 200
    assert "OAuth login service is reachable" in response.text

if __name__ == "__main__":
    try:
        test_oauth_login_service_wiring()
        print("PASS")
    except AssertionError:
        print("FAIL")