import json
import math
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def compute_shannon_entropy(probs: List[float]) -> float:
    """Compute Shannon entropy H = -sum(p * log2(p)) for p > 0."""
    entropy = 0.0
    for p in probs:
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def determine_stability(entropy: float) -> str:
    """Classify stability based on entropy thresholds."""
    if entropy < 0.2:
        return "low"
    elif entropy < 0.5:
        return "medium"
    else:
        return "high"


def fetch_axis_scores(session) -> List:
    """Fetch axis scores from app database."""
    return session.query(McpLlmAxisScore).all()


def fetch_server_registry(session) -> dict:
    """Fetch server registry mapping server_id to name."""
    servers = session.query(McpServerRegistry).all()
    return {s.server_id: s.name for s in servers}


def write_entropy_record(server_id: str, axis_name: str, entropy_score: float,
                         stability: str, p_top: float, scored_at: datetime) -> bool:
    """Write entropy record via write_service HTTP."""
    payload = {
        "table": "mcp_axis_entropy",
        "rows": [{
            "server_id": server_id,
            "axis_name": axis_name,
            "entropy_score": entropy_score,
            "stability": stability,
            "p_top": p_top,
            "scored_at": scored_at.isoformat() if scored_at else None
        }]
    }
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def send_heartbeat(service_name: str) -> bool:
    """Send heartbeat to service_health table."""
    payload = {
        "table": "service_health",
        "rows": [{
            "service": service_name,
            "last_heartbeat": datetime.now(timezone.utc).isoformat()
        }]
    }
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def run():
    """Main daemon loop - processes axis scores on 4-hour cadence."""
    service_name = "axis_entropy_scoring_consumer"
    cadence_seconds = 4 * 60 * 60
    heartbeat_interval = 60
    last_heartbeat = 0

    while True:
        start_time = time.time()

        try:
            session = next(get_session())
            axis_scores = fetch_axis_scores(session)
            server_registry = fetch_server_registry(session)

            for score in axis_scores:
                try:
                    probs = json.loads(score.probs) if isinstance(score.probs, str) else score.probs
                    if not probs or not isinstance(probs, list):
                        continue

                    entropy = compute_shannon_entropy(probs)
                    stability = determine_stability(entropy)
                    p_top = getattr(score, 'p_top', 0.0)

                    write_entropy_record(
                        server_id=score.server_id,
                        axis_name=score.axis_name,
                        entropy_score=entropy,
                        stability=stability,
                        p_top=p_top,
                        scored_at=score.scored_at
                    )
                except Exception:
                    continue
                finally:
                    session.close()

        except Exception:
            pass

        if time.time() - last_heartbeat >= heartbeat_interval:
            send_heartbeat(service_name)
            last_heartbeat = time.time()

        elapsed = time.time() - start_time
        sleep_time = max(1, cadence_seconds - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    test_cases = [
        {"name": "uniform", "probs": [0.125] * 8, "expected": 0.56},
        {"name": "peaked", "probs": [0.95, 0.016, 0.016, 0.005, 0.005, 0.001, 0.001, 0.001], "expected": 0.14},
        {"name": "bimodal", "probs": [0.45, 0.45, 0.025, 0.025, 0.0125, 0.0125, 0.0125, 0.0125], "expected": 0.19},
        {"name": "single", "probs": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "expected": 0.0},
        {"name": "two-value", "probs": [0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "expected": 0.31},
    ]

    all_passed = True
    for tc in test_cases:
        entropy = compute_shannon_entropy(tc["probs"])
        diff = abs(entropy - tc["expected"])
        if diff > 0.01:
            print(f"FAIL: {tc['name']} expected ~{tc['expected']}, got {entropy:.4f}")
            all_passed = False
        else:
            print(f"PASS: {tc['name']} = {entropy:.4f} (~{tc['expected']})")

    if all_passed:
        print("PASS")
    else:
        print("FAIL")