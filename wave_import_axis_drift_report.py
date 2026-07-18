import logging
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPLLMAxisScores

router = APIRouter()
logger = logging.getLogger(__name__)

class AxisDriftReport(BaseModel):
    max_class_share_delta: float
    degenerate: bool

class AxisDriftResponse(BaseModel):
    axis: str
    report: AxisDriftReport

class WaveDriftReport(BaseModel):
    axes: Dict[str, AxisDriftReport]

def get_pre_window_corpus(session: Session, axis: str) -> Dict[str, int]:
    """Get class distribution for an axis before the wave window."""
    query = session.query(
        MCPLLMAxisScores.label,
        MCPLLMAxisScores.axis,
        MCPLLMAxisScores.created_at
    ).filter(
        MCPLLMAxisScores.axis == axis,
        MCPLLMAxisScores.created_at < datetime.fromisoformat(ZO_WAVE_SINCE)
    ).all()

    class_counts = {}
    for row in query:
        label = row.label
        class_counts[label] = class_counts.get(label, 0) + 1
    return class_counts

def get_wave_corpus(session: Session, axis: str) -> Dict[str, int]:
    """Get class distribution for an axis within the wave window."""
    query = session.query(
        MCPLLMAxisScores.label,
        MCPLLMAxisScores.axis,
        MCPLLMAxisScores.created_at
    ).filter(
        MCPLLMAxisScores.axis == axis,
        MCPLLMAxisScores.created_at >= datetime.fromisoformat(ZO_WAVE_SINCE)
    ).all()

    class_counts = {}
    for row in query:
        label = row.label
        class_counts[label] = class_counts.get(label, 0) + 1
    return class_counts

def calculate_drift(pre_window: Dict[str, int], wave: Dict[str, int]) -> AxisDriftReport:
    """Calculate drift between pre-window and wave distributions."""
    total_pre = sum(pre_window.values())
    total_wave = sum(wave.values())

    if total_pre == 0 or total_wave == 0:
        return AxisDriftReport(max_class_share_delta=0.0, degenerate=False)

    max_delta = 0.0
    degenerate = False

    all_labels = set(pre_window.keys()).union(set(wave.keys()))

    for label in all_labels:
        pre_share = pre_window.get(label, 0) / total_pre
        wave_share = wave.get(label, 0) / total_wave
        delta = abs(wave_share - pre_share)
        if delta > max_delta:
            max_delta = delta

    # Check for degeneracy
    if total_wave > 0:
        max_wave_share = max(wave.values()) / total_wave
        if max_wave_share > 0.9:
            degenerate = True

    return AxisDriftReport(max_class_share_delta=max_delta, degenerate=degenerate)

@router.get("/wave-drift-report", response_model=WaveDriftReport)
async def get_wave_drift_report(
    session: Session = Depends(get_session)
) -> WaveDriftReport:
    """Generate axis drift report for the current wave window."""
    axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface"
    ]

    reports = {}

    for axis in axes:
        pre_window = get_pre_window_corpus(session, axis)
        wave = get_wave_corpus(session, axis)
        report = calculate_drift(pre_window, wave)
        reports[axis] = report

    return WaveDriftReport(axes=reports)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app

    # Override the session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    from app.models import Base
    Base.metadata.create_all(test_engine)

    test_session = TestSession()
    test_data = [
        MCPLLMAxisScores(
            id=1,
            server_id=1,
            axis="overall_risk",
            label="high",
            score=0.9,
            created_at=datetime.fromisoformat("2023-01-01T00:00:00")
        ),
        MCPLLMAxisScores(
            id=2,
            server_id=1,
            axis="overall_risk",
            label="medium",
            score=0.7,
            created_at=datetime.fromisoformat("2023-01-01T00:00:00")
        ),
        MCPLLMAxisScores(
            id=3,
            server_id=1,
            axis="overall_risk",
            label="high",
            score=0.9,
            created_at=datetime.fromisoformat("2023-01-02T00:00:00")
        ),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Set the wave since time for testing
    ZO_WAVE_SINCE = "2023-01-02T00:00:00"

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/wave-drift-report")
    assert response.status_code == 200
    report = response.json()

    # Verify the report structure
    assert "axes" in report
    assert "overall_risk" in report["axes"]
    assert "max_class_share_delta" in report["axes"]["overall_risk"]
    assert "degenerate" in report["axes"]["overall_risk"]

    print("PASS")