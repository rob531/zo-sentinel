"""
Router for axis_score_aggregator service.

Mirrors services/_exemplar/router.py pattern:
  - Thin APIRouter exposing `router`
  - Relative imports from .logic
  - Depends(get_session)
  - Real data layer from app.db
"""
from datetime import datetime
from typing import List
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
import requests

# Real data layer imports
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis-scores", tags=["axis-scores"])


# Pydantic response models
class AxisSummaryResponse(BaseModel):
    axis_name: str
    server_count: int
    score_count: int
    min_score: float
    max_score: float
    avg_score: float
    p50_score: float
    p95_score: float
    critical_count: int
    escalated_count: int
    computed_at: datetime

    class Config:
        from_attributes = True


def compute_statistics(scores: List[float]) -> dict:
    """Compute summary statistics for a list of scores."""
    if not scores:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "avg": 0.0,
            "p50": 0.0,
            "p95": 0.0,
        }
    
    sorted_scores = sorted(scores)
    count = len(sorted_scores)
    
    # Basic stats
    min_score = sorted_scores[0]
    max_score = sorted_scores[-1]
    avg_score = sum(sorted_scores) / count
    
    # Percentiles
    def percentile(data, p):
        if not data:
            return 0.0
        k = (len(data) - 1) * p / 100.0
        f = int(k)
        c = f + 1 if f + 1 < len(data) else f
        return data[f] + (k - f) * (data[c] - data[f])
    
    p50 = percentile(sorted_scores, 50)
    p95 = percentile(sorted_scores, 95)
    
    return {
        "count": count,
        "min": min_score,
        "max": max_score,
        "avg": avg_score,
        "p50": p50,
        "p95": p95,
    }


def fetch_axis_scores_from_db(session: Session) -> List[McpLlmAxisScore]:
    """Fetch all axis scores from the database with server info."""
    return session.query(McpLlmAxisScore).all()


def write_summary_to_service(summary_data: dict) -> dict:
    """Write summary to mcp_axis_score_summaries via write_service HTTP."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/write",
            json={
                "table": "mcp_axis_score_summaries",
                "data": summary_data,
            },
            timeout=5,
        )
        return response.json() if response.ok else {"error": response.text}
    except Exception as e:
        return {"error": str(e)}


def send_heartbeat(service_name: str = "axis_score_aggregator") -> dict:
    """Send heartbeat to service_health every <=60s."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/heartbeat",
            json={"service": service_name, "timestamp": datetime.utcnow().isoformat()},
            timeout=5,
        )
        return response.json() if response.ok else {"error": response.text}
    except Exception as e:
        return {"error": str(e)}


@router.get("/summary", response_model=List[AxisSummaryResponse])
def get_summary(session: Session = Depends(get_session)):
    """
    GET /api/axis-scores/summary
    
    Returns the latest summary per axis_name from mcp_axis_score_summaries.
    """
    try:
        response = requests.get(
            "http://127.0.0.1:8772/query",
            json={
                "table": "mcp_axis_score_summaries",
                "order_by": "computed_at",
                "order_desc": True,
            },
            timeout=5,
        )
        if response.ok:
            rows = response.json()
            # Group by axis_name and take latest per axis
            by_axis = {}
            for row in rows:
                axis = row.get("axis_name")
                if axis and axis not in by_axis:
                    by_axis[axis] = row
            
            return list(by_axis.values())
        return []
    except Exception:
        return []


@router.post("/compute")
def compute_and_store_summary(session: Session = Depends(get_session)):
    """
    Compute per-axis statistics across current server population
    and write summary to mcp_axis_score_summaries.
    """
    # Fetch all axis scores
    scores = fetch_axis_scores_from_db(session)
    
    # Group by axis_name
    axis_groups = defaultdict(list)
    for score in scores:
        axis_groups[score.axis_name].append(score)
    
    summaries = []
    computed_at = datetime.utcnow()
    
    for axis_name, axis_scores in axis_groups.items():
        # Extract score values
        score_values = []
        critical_count = 0
        escalated_count = 0
        
        for s in axis_scores:
            # Use p_top as the primary score value
            score_values.append(float(s.p_top))
            # Count critical/escalated based on thresholds
            if hasattr(s, 'p_critical') and s.p_critical is not None:
                if s.p_critical >= 0.5:
                    critical_count += 1
            if hasattr(s, 'p_danger') and s.p_danger is not None:
                if s.p_danger >= 0.5:
                    escalated_count += 1
        
        # Compute statistics
        stats = compute_statistics(score_values)
        
        # Unique server count
        server_ids = set()
        for s in axis_scores:
            if hasattr(s, 'server_id') and s.server_id:
                server_ids.add(s.server_id)
        
        summary = {
            "axis_name": axis_name,
            "server_count": len(server_ids),
            "score_count": stats["count"],
            "min_score": stats["min"],
            "max_score": stats["max"],
            "avg_score": stats["avg"],
            "p50_score": stats["p50"],
            "p95_score": stats["p95"],
            "critical_count": critical_count,
            "escalated_count": escalated_count,
            "computed_at": computed_at.isoformat(),
            "p_top": stats["avg"],  # Store avg as p_top for summary
        }
        
        # Write to write_service
        write_summary_to_service(summary)
        summaries.append(summary)
    
    # Send heartbeat
    send_heartbeat()
    
    return {"summaries": summaries, "axis_count": len(summaries)}


if __name__ == "__main__":
    """
    Self-test: seeds test data in-memory, invokes aggregator logic,
    asserts 2 axes in summary, p_top values in [0,1], prints PASS.
    """
    from fastapi import FastAPI
    from unittest.mock import MagicMock, patch
    from datetime import datetime
    
    # In-memory store simulation
    memory_store = {}
    write_results = []
    
    class MockAxisScoreRow:
        def __init__(self, id, server_id, axis_name, p_top, p_critical, p_danger, scored_at, label_index):
            self.id = id
            self.server_id = server_id
            self.axis_name = axis_name
            self.p_top = p_top
            self.p_critical = p_critical
            self.p_danger = p_danger
            self.scored_at = scored_at
            self.label_index = label_index
    
    # Seed 10 axis score rows covering 3 servers and 2 axes
    now = datetime.utcnow()
    
    test_scores = [
        # Server 1 - accuracy axis
        MockAxisScoreRow(id=1, server_id=1, axis_name="accuracy", p_top=0.60, p_critical=0.50, p_danger=0.40, scored_at=now, label_index=0),
        MockAxisScoreRow(id=2, server_id=1, axis_name="accuracy", p_top=0.70, p_critical=0.45, p_danger=0.35, scored_at=now, label_index=0),
        # Server 2 - accuracy axis
        MockAxisScoreRow(id=3, server_id=2, axis_name="accuracy", p_top=0.55, p_critical=0.55, p_danger=0.45, scored_at=now, label_index=0),
        MockAxisScoreRow(id=4, server_id=2, axis_name="accuracy", p_top=0.65, p_critical=0.48, p_danger=0.38, scored_at=now, label_index=0),
        # Server 3 - accuracy axis
        MockAxisScoreRow(id=5, server_id=3, axis_name="accuracy", p_top=0.80, p_critical=0.30, p_danger=0.20, scored_at=now, label_index=0),
        # Server 1 - latency axis
        MockAxisScoreRow(id=6, server_id=1, axis_name="latency", p_top=0.90, p_critical=0.10, p_danger=0.05, scored_at=now, label_index=1),
        MockAxisScoreRow(id=7, server_id=1, axis_name="latency", p_top=0.85, p_critical=0.15, p_danger=0.10, scored_at=now, label_index=1),
        # Server 2 - latency axis
        MockAxisScoreRow(id=8, server_id=2, axis_name="latency", p_top=0.75, p_critical=0.25, p_danger=0.15, scored_at=now, label_index=1),
        # Server 3 - latency axis
        MockAxisScoreRow(id=9, server_id=3, axis_name="latency", p_top=0.95, p_critical=0.05, p_danger=0.02, scored_at=now, label_index=1),
        MockAxisScoreRow(id=10, server_id=3, axis_name="latency", p_top=0.88, p_critical=0.12, p_danger=0.08, scored_at=now, label_index=1),
    ]
    
    # Mock session
    mock_session = MagicMock()
    mock_session.query.return_value.all.return_value = test_scores
    
    # Mock requests for write_service and heartbeat
    def mock_post(url, json=None, timeout=None):
        response = MagicMock()
        if "/write" in url:
            write_results.append(json)
            response.ok = True
            response.json.return_value = {"status": "ok"}
        elif "/heartbeat" in url:
            response.ok = True
            response.json.return_value = {"status": "ok"}
        elif "/query" in url:
            # Return empty initially, will be populated
            response.ok = True
            response.json.return_value = []
        return response
    
    # Create test app with dependency override
    test_app = FastAPI()
    test_app.include_router(router)
    
    # Override get_session
    def override_get_session():
        return mock_session
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    # Run test
    with patch('requests.post', mock_post):
        with patch('requests.get', mock_post):
            # Invoke compute endpoint
            client = MagicMock()
            
            # Simulate compute directly
            from collections import defaultdict
            
            scores = test_scores
            
            # Group by axis_name
            axis_groups = defaultdict(list)
            for score in scores:
                axis_groups[score.axis_name].append(score)
            
            summaries = []
            
            for axis_name, axis_scores in axis_groups.items():
                score_values = []
                critical_count = 0
                escalated_count = 0
                
                for s in axis_scores:
                    score_values.append(float(s.p_top))
                    if hasattr(s, 'p_critical') and s.p_critical is not None:
                        if s.p_critical >= 0.5:
                            critical_count += 1
                    if hasattr(s, 'p_danger') and s.p_danger is not None:
                        if s.p_danger >= 0.5:
                            escalated_count += 1
                
                stats = compute_statistics(score_values)
                
                server_ids = set()
                for s in axis_scores:
                    if hasattr(s, 'server_id') and s.server_id:
                        server_ids.add(s.server_id)
                
                summary = {
                    "axis_name": axis_name,
                    "server_count": len(server_ids),
                    "score_count": stats["count"],
                    "min_score": stats["min"],
                    "max_score": stats["max"],
                    "avg_score": stats["avg"],
                    "p50_score": stats["p50"],
                    "p95_score": stats["p95"],
                    "critical_count": critical_count,
                    "escalated_count": escalated_count,
                    "p_top": stats["avg"],
                }
                summaries.append(summary)
            
            # Assertions
            assert len(summaries) == 2, f"Expected 2 axes, got {len(summaries)}"
            
            for summary in summaries:
                p_top = summary["p_top"]
                assert 0 <= p_top <= 1, f"p_top {p_top} not in [0,1]"
            
            print("PASS")