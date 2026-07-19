from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
import requests
from datetime import datetime

router = APIRouter()

class TierStabilityMatrix(BaseModel):
    oscillations: int
    final_tier: str

class LadderConvergenceReport(BaseModel):
    convergence_stats: Dict[str, int]
    tier_stability_matrix: Dict[str, TierStabilityMatrix]
    avg_passes_to_converge: float
    generated_at: str

def get_convergence_stats(session: Session, min_waves: int = 3) -> Dict[str, int]:
    # Get servers with at least min_waves scoring waves
    servers = session.query(MCPServerRegistry.id).join(
        MCPLLMAxisScores,
        MCPServerRegistry.id == MCPLLMAxisScores.server_id
    ).group_by(MCPServerRegistry.id).having(
        "COUNT(*) >= :min_waves",
        {"min_waves": min_waves}
    ).all()

    server_ids = [server.id for server in servers]

    # Query ZoComputer for scoring waves
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": """
                SELECT server_id, wave_number, tier
                FROM mcp_signal_scores
                WHERE server_id IN :server_ids
                ORDER BY server_id, wave_number
            """,
            "params": {"server_ids": server_ids}
        }
    )

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to query ZoComputer")

    waves = response.json()

    # Process waves to determine convergence
    convergence_stats = {
        "servers_converged_in_1": 0,
        "servers_converged_in_2": 0,
        "servers_converged_in_3_plus": 0,
        "servers_never_converged": 0
    }

    tier_stability_matrix = {}
    total_passes = 0
    servers_processed = 0

    for server_id in server_ids:
        server_waves = [wave for wave in waves if wave["server_id"] == server_id]

        if not server_waves:
            continue

        tiers = [wave["tier"] for wave in server_waves]
        passes_to_converge = None

        for i in range(1, len(tiers)):
            if tiers[i] == tiers[i-1]:
                passes_to_converge = i
                break

        if passes_to_converge is None:
            convergence_stats["servers_never_converged"] += 1
            continue

        servers_processed += 1
        total_passes += passes_to_converge

        if passes_to_converge == 1:
            convergence_stats["servers_converged_in_1"] += 1
        elif passes_to_converge == 2:
            convergence_stats["servers_converged_in_2"] += 1
        else:
            convergence_stats["servers_converged_in_3_plus"] += 1

        # Update tier stability matrix
        final_tier = tiers[-1]
        oscillations = len([i for i in range(1, len(tiers)) if tiers[i] != tiers[i-1]])

        if final_tier not in tier_stability_matrix:
            tier_stability_matrix[final_tier] = TierStabilityMatrix(
                oscillations=0,
                final_tier=final_tier
            )

        tier_stability_matrix[final_tier].oscillations += oscillations

    avg_passes = total_passes / servers_processed if servers_processed > 0 else 0

    return {
        "convergence_stats": convergence_stats,
        "tier_stability_matrix": tier_stability_matrix,
        "avg_passes_to_converge": avg_passes
    }

@router.get("/ladder-convergence-report", response_model=LadderConvergenceReport)
async def get_ladder_convergence_report(
    session: Session = Depends(get_session),
    min_waves: Optional[int] = 3
):
    report_data = get_convergence_stats(session, min_waves)

    return LadderConvergenceReport(
        convergence_stats=report_data["convergence_stats"],
        tier_stability_matrix=report_data["tier_stability_matrix"],
        avg_passes_to_converge=report_data["avg_passes_to_converge"],
        generated_at=datetime.utcnow().isoformat()
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/scoring/ladder-convergence-report")
    assert response.status_code == 200
    data = response.json()

    assert "convergence_stats" in data
    assert "tier_stability_matrix" in data

    print("PASS")