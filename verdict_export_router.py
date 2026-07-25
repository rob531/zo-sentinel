from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
import csv
from io import StringIO
from app.db import get_session
from app.models import MCPLLMAxisScores
from app.dependency_overrides import dependency_overrides

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class VerdictExportResponse(BaseModel):
    scores: List[AxisScore]

def get_verdict_export(server_id: str, include_headers: bool = True) -> Response:
    session = next(get_session())

    try:
        scores = session.query(MCPLLMAxisScores).filter(
            MCPLLMAxisScores.server_id == server_id
        ).all()

        if not scores:
            raise HTTPException(status_code=404, detail="Server not found")

        output = StringIO()
        writer = csv.writer(output)

        if include_headers:
            writer.writerow([
                "axis_name", "label", "p_top", "p_critical", "p_danger"
            ])

        for score in scores:
            writer.writerow([
                score.axis_name,
                score.label,
                score.p_top,
                score.p_critical,
                score.p_danger
            ])

        output.seek(0)
        return Response(
            content=output.read(),
            media_type="text/csv"
        )
    finally:
        session.close()

router.get("/export/{server_id}")(get_verdict_export)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override get_session for testing
    def test_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    dependency_overrides[get_session] = test_get_session

    # Insert test data
    with TestSession() as session:
        test_server_id = "test-server-123"
        test_scores = [
            MCPLLMAxisScores(
                server_id=test_server_id,
                axis_name="overall_risk",
                label="Overall Risk",
                p_top=0.9,
                p_critical=0.8,
                p_danger=0.7
            ),
            MCPLLMAxisScores(
                server_id=test_server_id,
                axis_name="auth_strength",
                label="Auth Strength",
                p_top=0.85,
                p_critical=0.75,
                p_danger=0.65
            )
        ]
        session.add_all(test_scores)
        session.commit()

    client = TestClient(app)
    response = client.get(f"/export/{test_server_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv"

    csv_content = response.content.decode("utf-8")
    lines = csv_content.splitlines()
    assert len(lines) >= 2  # At least header and one data row
    assert lines[0] == "axis_name,label,p_top,p_critical,p_danger"

    print("PASS")