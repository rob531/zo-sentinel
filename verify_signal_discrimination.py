import requests
from typing import Dict, List
import json
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)

class VerifySignalDiscrimination:
    def __init__(self):
        self.write_service_url = "http://127.0.0.1:8772"
        self.signal_types = ["permission_scope", "temporal_stability", "tool_description_safe"]
        self.threshold = 5

    def run(self):
        results = {}
        for signal_type in self.signal_types:
            response = requests.post(
                f"{self.write_service_url}/write",
                json={'table': 'mcp_signal_scores', 'rows': [{'signal_type': signal_type}]},
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            scores = [item['score'] for item in data['rows']]
            results[signal_type] = len(set(scores))
        
        weak_signals = {}
        for signal_type, count in results.items():
            if count < self.threshold:
                weak_signals[signal_type] = (count, list(set(scores)))
        
        return weak_signals

if __name__ == '__main__':
    verify_signal_discrimination = VerifySignalDiscrimination()
    print(verify_signal_discrimination.run())