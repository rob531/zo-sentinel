import os
import time
import jwt
from urllib.parse import urlencode
from typing import Dict, Tuple, Optional

class OAuthLoginService:
    def __init__(self, write_service):
        self.write_service = write_service
        self.jwt_secret = os.environ.get('JWT_SECRET', 'default-secret-for-testing-only')
        self.jwt_algorithm = os.environ.get('JWT_ALGORITHM', 'HS256')
        self.jwt_expiration = int(os.environ.get('JWT_EXPIRATION', '3600'))

    def begin_oauth(self, provider: str) -> str:
        """Generate OAuth authorization URL for the given provider."""
        providers = {
            'google': {
                'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
                'redirect_uri': os.environ.get('GOOGLE_REDIRECT_URI'),
                'scope': 'email profile',
                'auth_url': 'https://accounts.google.com/o/oauth2/auth'
            },
            'github': {
                'client_id': os.environ.get('GITHUB_CLIENT_ID'),
                'redirect_uri': os.environ.get('GITHUB_REDIRECT_URI'),
                'scope': 'user:email',
                'auth_url': 'https://github.com/login/oauth/authorize'
            }
        }

        if provider not in providers:
            raise ValueError(f"Unsupported provider: {provider}")

        provider_config = providers[provider]
        params = {
            'client_id': provider_config['client_id'],
            'redirect_uri': provider_config['redirect_uri'],
            'response_type': 'code',
            'scope': provider_config['scope'],
            'state': 'random-state-string'
        }

        return f"{provider_config['auth_url']}?{urlencode(params)}"

    def complete_oauth(self, provider: str, code: str) -> Tuple[Dict, str]:
        """Exchange OAuth code for user info and JWT session."""
        # In a real implementation, this would make a network call to the provider's token endpoint
        # and then fetch user info. For this example, we'll simulate it.

        # Simulate user info from provider
        user_info = {
            'google': {'id': '123456789', 'email': 'user@example.com', 'name': 'Test User'},
            'github': {'id': '987654321', 'email': 'user@github.com', 'name': 'GitHub User'}
        }.get(provider, {})

        if not user_info:
            raise ValueError(f"Unsupported provider: {provider}")

        # Create or get user in our system (delegated to write_service)
        user = self.write_service.get_or_create_user(
            provider=provider,
            provider_id=user_info['id'],
            email=user_info['email'],
            name=user_info['name']
        )

        # Issue JWT session
        jwt_token = self.issue_session(user['id'])

        return user, jwt_token

    def verify_jwt(self, token: str) -> Optional[Dict]:
        """Verify JWT token and return claims if valid."""
        try:
            claims = jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])
            return claims
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None

    def issue_session(self, user_id: str) -> str:
        """Issue a new JWT session for the given user ID."""
        payload = {
            'user_id': user_id,
            'iat': int(time.time()),
            'exp': int(time.time()) + self.jwt_expiration
        }
        return jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)

if __name__ == '__main__':
    # Mock write_service for testing
    class MockWriteService:
        def get_or_create_user(self, provider, provider_id, email, name):
            return {'id': 'test-user-001', 'provider': provider, 'provider_id': provider_id, 'email': email, 'name': name}

    # Test the service
    service = OAuthLoginService(MockWriteService())

    # Test JWT round-trip
    jwt_token = service.issue_session('test-user-001')
    claims = service.verify_jwt(jwt_token)

    if claims and claims['user_id'] == 'test-user-001':
        print("PASS")
    else:
        print("FAIL")