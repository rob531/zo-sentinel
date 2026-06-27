import time
import requests
from datetime import datetime
from typing import List, Dict, Optional

# Mock dependencies for testing
class MockWriteService:
    def __init__(self):
        self.writes = []

    def write_risk_register(self, data: Dict) -> bool:
        self.writes.append(data)
        return True

class MockServiceHealth:
    def __init__(self):
        self.heartbeats = []

    def send_heartbeat(self, service_name: str) -> bool:
        self.heartbeats.append((service_name, datetime.now()))
        return True

# Main daemon class
class AppScoringConsumerOrchestrator:
    def __init__(self, write_service, service_health):
        self.write_service = write_service
        self.service_health = service_health
        self.last_heartbeat = datetime.now()

    def _send_heartbeat(self) -> None:
        if (datetime.now() - self.last_heartbeat).total_seconds() >= 60:
            self.service_health.send_heartbeat("app_scoring_consumer_orchestrator")
            self.last_heartbeat = datetime.now()

    def _get_new_scores(self) -> List[Dict]:
        # Simulate querying mcp_llm_axis_scores for new/updated entries
        # In a real implementation, this would query the database
        return [
            {"id": 1, "axis": "security", "score": 0.85, "timestamp": datetime.now()},
            {"id": 2, "axis": "privacy", "score": 0.72, "timestamp": datetime.now()}
        ]

    def _compute_risk_tier(self, score: float) -> str:
        # Simple risk tier computation logic
        if score >= 0.8:
            return "high"
        elif score >= 0.5:
            return "medium"
        else:
            return "low"

    def _process_scores(self, scores: List[Dict]) -> List[Dict]:
        processed = []
        for score in scores:
            risk_tier = self._compute_risk_tier(score["score"])
            processed.append({
                "id": score["id"],
                "axis": score["axis"],
                "score": score["score"],
                "risk_tier": risk_tier,
                "last_updated": datetime.now()
            })
        return processed

    def run(self) -> None:
        while True:
            self._send_heartbeat()

            scores = self._get_new_scores()
            if scores:
                processed = self._process_scores(scores)
                for entry in processed:
                    self.write_service.write_risk_register(entry)

            time.sleep(60)  # Run every 60 seconds

def run() -> None:
    # Initialize mock services
    write_service = MockWriteService()
    service_health = MockServiceHealth()

    # Create and run orchestrator
    orchestrator = AppScoringConsumerOrchestrator(write_service, service_health)

    # Simulate new scores and run one iteration
    orchestrator._get_new_scores = lambda: [
        {"id": 1, "axis": "security", "score": 0.85, "timestamp": datetime.now()},
        {"id": 2, "axis": "privacy", "score": 0.72, "timestamp": datetime.now()}
    ]
    orchestrator.run()

    # Assertions
    assert len(write_service.writes) == 2
    assert all("risk_tier" in entry for entry in write_service.writes)
    assert len(service_health.heartbeats) >= 1

    print("PASS")

if __name__ == '__main__':
    run()