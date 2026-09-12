from datetime import datetime
import time

from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy import select

from write_service import write_service


def run(app=None):
    audit_log = []
    prev_state = {}

    current_registry = write_service.query(
        table="McpServerRegistry",
        filter_criteria={}
    )

    for server in current_registry:
        sid = server["server_id"]
        current_tier = server.get("risk_tier", "none")
        if sid in prev_state:
            if prev_state[sid] != current_tier:
                audit_log.append({
                    "server_id": sid,
                    "old_tier": prev_state[sid],
                    "new_tier": current_tier,
                    "timestamp": datetime.now().isoformat()
                })
        prev_state[sid] = current_tier

    return audit_log


def main():
    from fastapi import FastAPI
    from sqlalchemy.orm import Session

    app = FastAPI()

    class MockResult:
        def __init__(self, servers):
            self._servers = servers
        def all(self):
            return self._servers

    class MockSession:
        def __init__(self, servers):
            self._servers = servers
        def exec(self, *args, **kwargs):
            return MockResult(self._servers)
        def close(self):
            pass

    def override_get_session():
        servers = [
            McpServerRegistry(
                server_id="server1",
                risk_tier="low",
                name="Server One",
                url="http://server1.example.com",
                registry_source="test"
            ),
            McpServerRegistry(
                server_id="server2",
                risk_tier="medium",
                name="Server Two",
                url="http://server2.example.com",
                registry_source="test"
            ),
        ]
        return MockSession(servers)

    app.dependency_overrides[get_session] = override_get_session

    from unittest.mock import patch

    seed_state = {
        "server1": {"server_id": "server1", "risk_tier": "low"},
        "server2": {"server_id": "server2", "risk_tier": "medium"},
    }
    query_count = [0]

    def mock_query(table, filter_criteria):
        query_count[0] += 1
        if query_count[0] == 1:
            return list(seed_state.values())
        else:
            return [
                {"server_id": "server1", "risk_tier": "high"},
                {"server_id": "server2", "risk_tier": "medium"},
            ]

    with patch.object(write_service, 'query', side_effect=mock_query):
        result = run(app)

    assert len(result) == 1, f"Expected 1 audit entry, got {len(result)}"
    assert result[0]["server_id"] == "server1"
    assert result[0]["old_tier"] == "low"
    assert result[0]["new_tier"] == "high"

    print("PASS")


if __name__ == "__main__":
    main()