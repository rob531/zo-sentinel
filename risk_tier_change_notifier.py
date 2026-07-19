import time
import json
import requests
from datetime import datetime
from typing import Optional
from app.db import get_session
from app.models import ServerRegistry, LLMScore
from sqlalchemy.orm import Session
from sqlalchemy import func

class RiskTierChangeNotifier:
    def __init__(self, webhook_url: str, poll_interval: int = 60):
        self.webhook_url = webhook_url
        self.poll_interval = poll_interval
        self.last_checked = None
        self.retry_delay = 1

    def _get_current_tiers(self, db: Session) -> dict:
        current_tiers = {}
        servers = db.query(ServerRegistry).all()
        for server in servers:
            scores = db.query(LLMScore).filter(LLMScore.server_id == server.id).all()
            if not scores:
                continue
            avg_score = sum(score.value for score in scores) / len(scores)
            if avg_score < 0.3:
                tier = "LOW"
            elif avg_score < 0.7:
                tier = "MEDIUM"
            else:
                tier = "HIGH"
            current_tiers[server.id] = tier
        return current_tiers

    def _post_to_webhook(self, payload: dict) -> bool:
        for attempt in range(3):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                if response.status_code == 200:
                    self.retry_delay = 1
                    return True
            except requests.RequestException:
                pass
            time.sleep(self.retry_delay)
            self.retry_delay *= 2
        self.retry_delay = 1
        return False

    def run(self) -> None:
        while True:
            try:
                db = next(get_session())
                current_tiers = self._get_current_tiers(db)
                if self.last_checked is None:
                    self.last_checked = current_tiers
                else:
                    for server_id, new_tier in current_tiers.items():
                        if server_id in self.last_checked and self.last_checked[server_id] != new_tier:
                            payload = {
                                "server_id": server_id,
                                "old_tier": self.last_checked[server_id],
                                "new_tier": new_tier,
                                "changed_at": datetime.utcnow().isoformat()
                            }
                            self._post_to_webhook(payload)
                self.last_checked = current_tiers
            except Exception as e:
                print(f"Error during poll: {e}")
            finally:
                db.close()
            time.sleep(self.poll_interval)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI, Depends
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    test_db = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_db)
    Base.metadata.create_all(test_db)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.post("/query")
    async def mock_query():
        return {"results": []}

    client = TestClient(app)

    notifier = RiskTierChangeNotifier("http://localhost:8000/alert", poll_interval=1)
    notifier.last_checked = {"test_server": "LOW"}
    notifier._get_current_tiers = lambda db: {"test_server": "HIGH"}

    with client:
        notifier.run()

    print("PASS")