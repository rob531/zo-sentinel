from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from typing import List, Dict, Any
import csv
import io
import json
from pydantic import BaseModel

router = APIRouter()

class EntityReport(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class EntityReportResponse(BaseModel):
    data: List[EntityReport]
    overall_risk: float

def get_entity_reports(db: Session, entity_id: str) -> List[EntityReport]:
    query = db.query(
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.label,
        MCPLLMAxisScores.p_top,
        MCPLLMAxisScores.p_critical,
        MCPLLMAxisScores.p_danger
    ).join(
        MCPServerRegistry, MCPServerRegistry.id == MCPLLMAxisScores.server_id
    ).filter(
        MCPServerRegistry.id == entity_id
    ).all()

    if not query:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entity not found"
        )

    return [EntityReport(**row._asdict()) for row in query]

@router.get("/export/csv/{entity_id}", response_class=StreamingResponse)
async def export_entity_report_csv(entity_id: str, db: Session = Depends(get_session)):
    reports = get_entity_reports(db, entity_id)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "axis_name", "label", "p_top", "p_critical", "p_danger"
    ])
    writer.writeheader()
    for report in reports:
        writer.writerow(report.dict())

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=entity_{entity_id}_report.csv"}
    )

@router.get("/export/json/{entity_id}", response_class=JSONResponse)
async def export_entity_report_json(entity_id: str, db: Session = Depends(get_session)):
    reports = get_entity_reports(db, entity_id)

    overall_risk = sum(report.p_danger for report in reports) / len(reports) if reports else 0.0

    return EntityReportResponse(
        data=[report.dict() for report in reports],
        overall_risk=overall_risk
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from unittest.mock import patch
    import requests

    test_client = TestClient(app)

    mock_data = [
        {
            "axis_name": "axis1",
            "label": "label1",
            "p_top": 0.1,
            "p_critical": 0.2,
            "p_danger": 0.3
        },
        {
            "axis_name": "axis2",
            "label": "label2",
            "p_top": 0.4,
            "p_critical": 0.5,
            "p_danger": 0.6
        }
    ]

    def mock_post(*args, **kwargs):
        return requests.Response()
    mock_post.json.return_value = {"data": mock_data}

    with patch("requests.post", new=mock_post):
        response_csv = test_client.get("/export/csv/test_entity")
        assert response_csv.status_code == 200
        assert response_csv.headers["content-type"] == "text/csv"
        csv_data = response_csv.text.splitlines()
        assert csv_data[0] == "axis_name,label,p_top,p_critical,p_danger"

        response_json = test_client.get("/export/json/test_entity")
        assert response_json.status_code == 200
        json_data = response_json.json()
        assert "data" in json_data
        assert "overall_risk" in json_data
        assert len(json_data["data"]) == 2

    print("PASS")