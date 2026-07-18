import os
import time
from datetime import datetime
from typing import Dict, Optional

import httpx
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScores

app = FastAPI()

def get_write_service_client():
    return httpx.AsyncClient(base_url="http://127.0.0.1:8772")

async def get_scored_rows_count(session: Session) -> int:
    return session.query(McpLlmAxisScores).count()

async def get_run_state_mtime() -> Optional[float]:
    run_state_path = os.getenv("ZO_RUN_STATE", "/tmp/run_state.json")
    try:
        return os.path.getmtime(run_state_path)
    except OSError:
        return None

async def probe_import_liveness() -> Dict[str, str]:
    interval = int(os.getenv("ZO_PROBE_INTERVAL", "60"))
    client = get_write_service_client()

    # First sample
    first_sample = {
        "scored_rows": await get_scored_rows_count(Depends(get_session)),
        "mtime": await get_run_state_mtime(),
        "timestamp": datetime.utcnow().isoformat()
    }

    # Wait for interval
    time.sleep(interval)

    # Second sample
    second_sample = {
        "scored_rows": await get_scored_rows_count(Depends(get_session)),
        "mtime": await get_run_state_mtime(),
        "timestamp": datetime.utcnow().isoformat()
    }

    # Determine verdict
    if (second_sample["scored_rows"] > first_sample["scored_rows"] or
        second_sample["mtime"] != first_sample["mtime"]):
        verdict = "IMPORT_ALIVE"
    else:
        verdict = "IMPORT_STALLED"

    return {
        "verdict": verdict,
        "first_sample": first_sample,
        "second_sample": second_sample
    }

@app.get("/probe")
async def probe() -> Dict[str, str]:
    return await probe_import_liveness()

if __name__ == "__main__":
    import asyncio
    from app.db import SessionLocal

    # Override session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data setup
    test_db = SessionLocal()
    test_db.query(McpLlmAxisScores).delete()
    test_db.add(McpLlmAxisScores(
        overall_risk=0.5,
        auth_strength=0.3,
        capability_breadth=0.7,
        data_sensitivity=0.4,
        network_egress=0.6,
        maintainer_trust=0.8,
        exploit_surface=0.2
    ))
    test_db.commit()

    # Create test run_state file
    with open("/tmp/run_state.json", "w") as f:
        f.write("{}")

    # Test probe
    async def test_probe():
        result = await probe_import_liveness()
        assert result["verdict"] == "IMPORT_ALIVE"
        print("PASS")

    asyncio.run(test_probe())