import os
from typing import Dict

class BuildEnvConfig:
    def __init__(self):
        self.write_service_url = os.getenv('WRITE_SERVICE_URL', 'http://127.0.0.1:8772')
        self.ollama_url = os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434')
        self.minimax_api_key = os.getenv('MINIMAX_API_KEY', '')
        self.github_token = os.getenv('GITHUB_TOKEN', '')
        self.webhook_secret = os.getenv('WEBHOOK_SECRET', '')

    def get_config(self) -> Dict:
        return {
            'WRITE_SERVICE_URL': self.write_service_url,
            'OLLAMA_URL': self.ollama_url,
            'MINIMAX_API_KEY': self.minimax_api_key,
            'GITHUB_TOKEN': self.github_token,
            'WEBHOOK_SECRET': self.webhook_secret
        }

if __name__ == '__main__':
    build_config = BuildEnvConfig()
    print(build_config.get_config())