# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from fastapi import Depends
import requests

# deps: requests

async def get_signal_scores(server_id: int, session: Depends(get_session)):
    """Fetch signal scores for a given server."""
    scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    return scores

async def _run_self_test():
    """Run self-test for the service."""
    test_server_id = 1
    test_scores = await get_signal_scores(test_server_id)
    assert len(test_scores) > 0, "Self-test failed: no scores found"
    print("Self-test passed")

if __name__ == "__main__":
    import asyncio
    asyncio.run(_run_self_test())
