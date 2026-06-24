import requests

class OAuthLoginService:
    def __init__(self, auth_server_url):
        self.auth_server_url = auth_server_url

    def login(self, user_credentials):
        username = user_credentials.get('username')
        password = user_credentials.get('password')
        client_id = user_credentials.get('client_id')

        if not all([username, password, client_id]):
            raise ValueError("Missing required credentials")

        response = requests.post(
            f"{self.auth_server_url}/token",
            data={
                'grant_type': 'password',
                'username': username,
                'password': password,
                'client_id': client_id
            }
        )

        if response.status_code != 200:
            raise Exception(f"Authentication failed: {response.text}")

        tokens = response.json()
        access_token = tokens.get('access_token')
        refresh_token = tokens.get('refresh_token')

        if not all([access_token, refresh_token]):
            raise Exception("Invalid tokens received")

        return access_token, refresh_token

if __name__ == "__main__":
    auth_server_url = "https://example.com/auth"
    oauth_service = OAuthLoginService(auth_server_url)

    try:
        access_token, refresh_token = oauth_service.login({
            'username': 'test_user',
            'password': 'test_pass',
            'client_id': 'test_client'
        })

        assert access_token and refresh_token
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")