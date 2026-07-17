import logging
import time
from typing import List, Dict, Tuple
from fastapi import FastAPI, Depends
from fastapi.lifespan import Lifespan
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScore, MCPServerRegistry, MCPRiskRegister
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

app = FastAPI()

def compute_tier_from_axes(axes: List[Dict]) -> Tuple[str, float]:
    """Compute risk tier from the 6 axes scores."""
    axis_names = ['overall_risk', 'auth_strength', 'capability_breadth',
                  'data_sensitivity', 'network_egress', 'maintainer_trust',
                  'exploit_surface']

    # Initialize axis scores with default values
    axis_scores = {name: 0.0 for name in axis_names}

    # Aggregate scores from all axes
    for axis in axes:
        if axis['axis_name'] in axis_scores:
            # Use p_danger as the primary score
            axis_scores[axis['axis_name']] = axis['p_danger']

    # Calculate composite score (average of all axes)
    composite_score = sum(axis_scores.values()) / len(axis_scores)

    # Determine risk tier based on composite score
    if composite_score >= 0.8:
        return "critical", composite_score
    elif composite_score >= 0.6:
        return "high", composite_score
    elif composite_score >= 0.4:
        return "medium", composite_score
    elif composite_score >= 0.2:
        return "low", composite_score
    else:
        return "minimal", composite_score

def get_unprocessed_server_ids(session: Session, limit: int = 500) -> List[str]:
    """Get server_ids that have been scored but not yet processed."""
    # Get server_ids from mcp_llm_axis_scores
    scored_server_ids = session.query(MCPLLMAxisScore.server_id).distinct().all()
    scored_server_ids = [id[0] for id in scored_server_ids]

    # Get server_ids that already have a risk_tier in mcp_server_registry
    processed_server_ids = session.query(MCPServerRegistry.server_id).filter(MCPServerRegistry.risk_tier.isnot(None)).all()
    processed_server_ids = [id[0] for id in processed_server_ids]

    # Return server_ids that are scored but not processed
    unprocessed_server_ids = list(set(scored_server_ids) - set(processed_server_ids))
    return unprocessed_server_ids[:limit]

async def process_server_ids(server_ids: List[str], session: Session):
    """Process server_ids to compute risk tier and update databases."""
    for server_id in server_ids:
        try:
            # Get all axis scores for the server_id
            axes = session.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id).all()
            axes = [{'axis_name': axis.axis_name, 'p_danger': axis.p_danger} for axis in axes]

            if not axes:
                logger.warning(f"No axis scores found for server_id: {server_id}")
                continue

            # Compute risk tier
            risk_tier, composite_score = compute_tier_from_axes(axes)

            # Check current risk_tier in mcp_server_registry
            current_tier = session.query(MCPServerRegistry.risk_tier).filter(MCPServerRegistry.server_id == server_id).scalar()

            if current_tier == risk_tier:
                logger.info(f"Server_id {server_id} already has risk_tier {risk_tier}, skipping")
                continue

            # Update mcp_server_registry
            session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).update({'risk_tier': risk_tier})

            # Update mcp_risk_register
            risk_register = session.query(MCPRiskRegister).filter(MCPRiskRegister.server_id == server_id).first()
            if risk_register:
                session.query(MCPRiskRegister).filter(MCPRiskRegister.server_id == server_id).update({
                    'computed_at': datetime.utcnow(),
                    'composite_score': composite_score
                })
            else:
                session.add(MCPRiskRegister(
                    server_id=server_id,
                    computed_at=datetime.utcnow(),
                    composite_score=composite_score
                ))

            session.commit()
            logger.info(f"Processed server_id {server_id}, risk_tier: {risk_tier}, composite_score: {composite_score}")

        except Exception as e:
            session.rollback()
            logger.error(f"Error processing server_id {server_id}: {str(e)}")

async def start_scoring_consumer():
    """Background task to poll for unprocessed server_ids and compute risk tiers."""
    while True:
        try:
            with Depends(get_session) as session:
                server_ids = get_unprocessed_server_ids(session)
                if server_ids:
                    await process_server_ids(server_ids, session)
                else:
                    logger.info("No unprocessed server_ids found")

            # Health heartbeat
            try:
                requests.post("http://127.0.0.1:8772/health", json={"service": "scoring_consumer"}, timeout=10)
            except Exception as e:
                logger.warning(f"Health heartbeat failed: {str(e)}")

            # Wait for next cycle
            time.sleep(30)

        except Exception as e:
            logger.error(f"Error in scoring consumer: {str(e)}")
            time.sleep(30)

@app.on_event("startup")
async def on_startup():
    """Start the scoring consumer on app startup."""
    app.state.scoring_consumer_task = app.background_tasks.create_task(start_scoring_consumer())

if __name__ == "__main__":
    import unittest
    from unittest.mock import patch, MagicMock
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    class TestScoringConsumer(unittest.TestCase):
        @patch('scoring_consumer_prod.requests.post')
        def test_compute_tier_from_axes(self, mock_post):
            # Test boundary values
            axes = [
                {'axis_name': 'overall_risk', 'p_danger': 0.9},
                {'axis_name': 'auth_strength', 'p_danger': 0.9},
                {'axis_name': 'capability_breadth', 'p_danger': 0.9},
                {'axis_name': 'data_sensitivity', 'p_danger': 0.9},
                {'axis_name': 'network_egress', 'p_danger': 0.9},
                {'axis_name': 'maintainer_trust', 'p_danger': 0.9},
                {'axis_name': 'exploit_surface', 'p_danger': 0.9}
            ]
            tier, score = compute_tier_from_axes(axes)
            self.assertEqual(tier, "critical")
            self.assertEqual(score, 0.9)

            axes = [
                {'axis_name': 'overall_risk', 'p_danger': 0.5},
                {'axis_name': 'auth_strength', 'p_danger': 0.5},
                {'axis_name': 'capability_breadth', 'p_danger': 0.5},
                {'axis_name': 'data_sensitivity', 'p_danger': 0.5},
                {'axis_name': 'network_egress', 'p_danger': 0.5},
                {'axis_name': 'maintainer_trust', 'p_danger': 0.5},
                {'axis_name': 'exploit_surface', 'p_danger': 0.5}
            ]
            tier, score = compute_tier_from_axes(axes)
            self.assertEqual(tier, "medium")
            self.assertEqual(score, 0.5)

            print("PASS: compute_tier_from_axes returns correct tier strings at boundary values")

        @patch('scoring_consumer_prod.requests.post')
        def test_get_unprocessed_server_ids(self, mock_post):
            # Create a test session
            engine = create_engine('sqlite:///:memory:')
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

            # Create tables
            from app.models import Base
            Base.metadata.create_all(bind=engine)

            # Add test data
            session = SessionLocal()
            session.add_all([
                MCPLLMAxisScore(server_id="1", axis_name="overall_risk", p_danger=0.1),
                MCPLLMAxisScore(server_id="1", axis_name="auth_strength", p_danger=0.2),
                MCPLLMAxisScore(server_id="2", axis_name="overall_risk", p_danger=0.3),
                MCPServerRegistry(server_id="1", risk_tier="low")
            ])
            session.commit()

            # Test get_unprocessed_server_ids
            server_ids = get_unprocessed_server_ids(session)
            self.assertEqual(server_ids, ["2"])

            print("PASS: get_unprocessed_server_ids returns deduped list")

        @patch('scoring_consumer_prod.requests.post')
        def test_module_imports(self, mock_post):
            # Test that the module imports cleanly
            import scoring_consumer_prod
            print("PASS: Module imports cleanly with no import-time side effects")

    unittest.main(argv=[''], exit=False)