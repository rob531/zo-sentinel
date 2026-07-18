import requests
from typing import List, Dict, Tuple
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from fastapi import Depends
from sqlalchemy.orm import Session

CANARY_CORPUS = [
    {
        "name": "Canary Server 1",
        "url": "https://canary1.example.com",
        "description": "Test server for canary checks",
        "tool_count": 5,
        "trust_level": 0.8,
        "expected_ranges": {
            "reliability": (0.7, 0.9),
            "trustworthiness": (0.75, 0.85),
            "capability": (0.6, 0.8),
            "safety": (0.7, 0.9),
            "privacy": (0.65, 0.85)
        }
    },
    {
        "name": "Canary Server 2",
        "url": "https://canary2.example.com",
        "description": "Another test server",
        "tool_count": 3,
        "trust_level": 0.6,
        "expected_ranges": {
            "reliability": (0.6, 0.8),
            "trustworthiness": (0.65, 0.75),
            "capability": (0.5, 0.7),
            "safety": (0.6, 0.8),
            "privacy": (0.6, 0.8)
        }
    },
    {
        "name": "Canary Server 3",
        "url": "https://canary3.example.com",
        "description": "Third canary server",
        "tool_count": 8,
        "trust_level": 0.9,
        "expected_ranges": {
            "reliability": (0.8, 0.95),
            "trustworthiness": (0.85, 0.95),
            "capability": (0.7, 0.9),
            "safety": (0.8, 0.95),
            "privacy": (0.75, 0.9)
        }
    },
    {
        "name": "Canary Server 4",
        "url": "https://canary4.example.com",
        "description": "Fourth test server",
        "tool_count": 2,
        "trust_level": 0.5,
        "expected_ranges": {
            "reliability": (0.5, 0.7),
            "trustworthiness": (0.55, 0.65),
            "capability": (0.4, 0.6),
            "safety": (0.5, 0.7),
            "privacy": (0.5, 0.7)
        }
    },
    {
        "name": "Canary Server 5",
        "url": "https://canary5.example.com",
        "description": "Fifth canary server",
        "tool_count": 6,
        "trust_level": 0.7,
        "expected_ranges": {
            "reliability": (0.65, 0.85),
            "trustworthiness": (0.7, 0.8),
            "capability": (0.55, 0.75),
            "safety": (0.65, 0.85),
            "privacy": (0.6, 0.8)
        }
    }
]

def get_server_scores(db: Session, server_name: str) -> Dict[str, float]:
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.name == server_name).first()
    if not server:
        return {}

    scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server.id).first()
    if not scores:
        return {}

    return {
        "reliability": scores.reliability,
        "trustworthiness": scores.trustworthiness,
        "capability": scores.capability,
        "safety": scores.safety,
        "privacy": scores.privacy
    }

def run_canary_checks() -> Dict[str, bool | List[Dict]]:
    checks = []
    all_passed = True

    db = Depends(get_session)()

    for server in CANARY_CORPUS:
        scores = get_server_scores(db, server["name"])
        if not scores:
            continue

        for axis, expected_range in server["expected_ranges"].items():
            actual = scores.get(axis, 0.0)
            passed = expected_range[0] <= actual <= expected_range[1]

            if not passed:
                all_passed = False

            checks.append({
                "name": server["name"],
                "axis": axis,
                "expected_range": expected_range,
                "actual": actual,
                "passed": passed
            })

    result = {
        "passed": all_passed,
        "checks": checks
    }

    # Log results to write_service
    try:
        requests.post("http://127.0.0.1:8772/query", json={
            "action": "log_canary_checks",
            "data": result
        })
    except requests.RequestException:
        pass

    return result

if __name__ == '__main__':
    result = run_canary_checks()
    assert result["passed"] == True
    print("CANARY PASS")