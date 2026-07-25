from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users

app = FastAPI()

class CVEData(BaseModel):
    cve_id: str
    description: str
    severity: str
    ecosystem: str
    published_date: str
    last_modified_date: str
    cvss_score: float
    mcp_server_count: int
    mcp_llm_axis_score: float
    dispute_count: int
    org_count: int

class CVEFilter(BaseModel):
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    min_cvss_score: Optional[float] = None
    max_cvss_score: Optional[float] = None
    min_mcp_llm_axis_score: Optional[float] = None
    max_mcp_llm_axis_score: Optional[float] = None
    min_mcp_server_count: Optional[int] = None
    max_mcp_server_count: Optional[int] = None
    min_dispute_count: Optional[int] = None
    max_dispute_count: Optional[int] = None
    min_org_count: Optional[int] = None
    max_org_count: Optional[int] = None

def get_cve_data(db: Session = Depends(get_session)) -> List[CVEData]:
    # Query MCP server registry for CVE data
    cve_data = db.query(
        MCPServerRegistry.cve_id,
        MCPServerRegistry.description,
        MCPServerRegistry.severity,
        MCPServerRegistry.ecosystem,
        MCPServerRegistry.published_date,
        MCPServerRegistry.last_modified_date,
        MCPServerRegistry.cvss_score,
        func.count(MCPServerRegistry.id).label('mcp_server_count'),
        MCPLLMAxisScores.score.label('mcp_llm_axis_score'),
        func.count(MCPScoreDisputes.id).label('dispute_count'),
        func.count(Orgs.id).label('org_count')
    ).join(
        MCPLLMAxisScores, MCPServerRegistry.cve_id == MCPLLMAxisScores.cve_id, isouter=True
    ).join(
        MCPScoreDisputes, MCPServerRegistry.cve_id == MCPScoreDisputes.cve_id, isouter=True
    ).join(
        Orgs, MCPServerRegistry.org_id == Orgs.id, isouter=True
    ).group_by(
        MCPServerRegistry.cve_id,
        MCPServerRegistry.description,
        MCPServerRegistry.severity,
        MCPServerRegistry.ecosystem,
        MCPServerRegistry.published_date,
        MCPServerRegistry.last_modified_date,
        MCPServerRegistry.cvss_score,
        MCPLLMAxisScores.score
    ).all()

    return [
        CVEData(
            cve_id=cve.cve_id,
            description=cve.description,
            severity=cve.severity,
            ecosystem=cve.ecosystem,
            published_date=str(cve.published_date),
            last_modified_date=str(cve.last_modified_date),
            cvss_score=cve.cvss_score,
            mcp_server_count=cve.mcp_server_count,
            mcp_llm_axis_score=cve.mcp_llm_axis_score if cve.mcp_llm_axis_score is not None else 0.0,
            dispute_count=cve.dispute_count,
            org_count=cve.org_count
        ) for cve in cve_data
    ]

@app.get("/cvss", response_model=List[CVEData])
def get_cvss_data(
    severity: Optional[str] = Query(None),
    ecosystem: Optional[str] = Query(None),
    min_cvss_score: Optional[float] = Query(None),
    max_cvss_score: Optional[float] = Query(None),
    min_mcp_llm_axis_score: Optional[float] = Query(None),
    max_mcp_llm_axis_score: Optional[float] = Query(None),
    min_mcp_server_count: Optional[int] = Query(None),
    max_mcp_server_count: Optional[int] = Query(None),
    min_dispute_count: Optional[int] = Query(None),
    max_dispute_count: Optional[int] = Query(None),
    min_org_count: Optional[int] = Query(None),
    max_org_count: Optional[int] = Query(None),
    db: Session = Depends(get_session)
):
    filters = []
    if severity:
        filters.append(MCPServerRegistry.severity == severity)
    if ecosystem:
        filters.append(MCPServerRegistry.ecosystem == ecosystem)
    if min_cvss_score is not None:
        filters.append(MCPServerRegistry.cvss_score >= min_cvss_score)
    if max_cvss_score is not None:
        filters.append(MCPServerRegistry.cvss_score <= max_cvss_score)
    if min_mcp_llm_axis_score is not None:
        filters.append(MCPLLMAxisScores.score >= min_mcp_llm_axis_score)
    if max_mcp_llm_axis_score is not None:
        filters.append(MCPLLMAxisScores.score <= max_mcp_llm_axis_score)
    if min_mcp_server_count is not None:
        filters.append(func.count(MCPServerRegistry.id) >= min_mcp_server_count)
    if max_mcp_server_count is not None:
        filters.append(func.count(MCPServerRegistry.id) <= max_mcp_server_count)
    if min_dispute_count is not None:
        filters.append(func.count(MCPScoreDisputes.id) >= min_dispute_count)
    if max_dispute_count is not None:
        filters.append(func.count(MCPScoreDisputes.id) <= max_dispute_count)
    if min_org_count is not None:
        filters.append(func.count(Orgs.id) >= min_org_count)
    if max_org_count is not None:
        filters.append(func.count(Orgs.id) <= max_org_count)

    query = db.query(
        MCPServerRegistry.cve_id,
        MCPServerRegistry.description,
        MCPServerRegistry.severity,
        MCPServerRegistry.ecosystem,
        MCPServerRegistry.published_date,
        MCPServerRegistry.last_modified_date,
        MCPServerRegistry.cvss_score,
        func.count(MCPServerRegistry.id).label('mcp_server_count'),
        MCPLLMAxisScores.score.label('mcp_llm_axis_score'),
        func.count(MCPScoreDisputes.id).label('dispute_count'),
        func.count(Orgs.id).label('org_count')
    ).join(
        MCPLLMAxisScores, MCPServerRegistry.cve_id == MCPLLMAxisScores.cve_id, isouter=True
    ).join(
        MCPScoreDisputes, MCPServerRegistry.cve_id == MCPScoreDisputes.cve_id, isouter=True
    ).join(
        Orgs, MCPServerRegistry.org_id == Orgs.id, isouter=True
    ).group_by(
        MCPServerRegistry.cve_id,
        MCPServerRegistry.description,
        MCPServerRegistry.severity,
        MCPServerRegistry.ecosystem,
        MCPServerRegistry.published_date,
        MCPServerRegistry.last_modified_date,
        MCPServerRegistry.cvss_score,
        MCPLLMAxisScores.score
    )

    if filters:
        query = query.filter(and_(*filters))

    cve_data = query.all()

    return [
        CVEData(
            cve_id=cve.cve_id,
            description=cve.description,
            severity=cve.severity,
            ecosystem=cve.ecosystem,
            published_date=str(cve.published_date),
            last_modified_date=str(cve.last_modified_date),
            cvss_score=cve.cvss_score,
            mcp_server_count=cve.mcp_server_count,
            mcp_llm_axis_score=cve.mcp_llm_axis_score if cve.mcp_llm_axis_score is not None else 0.0,
            dispute_count=cve.dispute_count,
            org_count=cve.org_count
        ) for cve in cve_data
    ]

@app.get("/cvss/{cve_id}", response_model=CVEData)
def get_cve_details(cve_id: str, db: Session = Depends(get_session)):
    cve_data = db.query(
        MCPServerRegistry.cve_id,
        MCPServerRegistry.description,
        MCPServerRegistry.severity,
        MCPServerRegistry.ecosystem,
        MCPServerRegistry.published_date,
        MCPServerRegistry.last_modified_date,
        MCPServerRegistry.cvss_score,
        func.count(MCPServerRegistry.id).label('mcp_server_count'),
        MCPLLMAxisScores.score.label('mcp_llm_axis_score'),
        func.count(MCPScoreDisputes.id).label('dispute_count'),
        func.count(Orgs.id).label('org_count')
    ).join(
        MCPLLMAxisScores, MCPServerRegistry.cve_id == MCPLLMAxisScores.cve_id, isouter=True
    ).join(
        MCPScoreDisputes, MCPServerRegistry.cve_id == MCPScoreDisputes.cve_id, isouter=True
    ).join(
        Orgs, MCPServerRegistry.org_id == Orgs.id, isouter=True
    ).filter(
        MCPServerRegistry.cve_id == cve_id
    ).group_by(
        MCPServerRegistry.cve_id,
        MCPServerRegistry.description,
        MCPServerRegistry.severity,
        MCPServerRegistry.ecosystem,
        MCPServerRegistry.published_date,
        MCPServerRegistry.last_modified_date,
        MCPServerRegistry.cvss_score,
        MCPLLMAxisScores.score
    ).first()

    if not cve_data:
        raise HTTPException(status_code=404, detail="CVE not found")

    return CVEData(
        cve_id=cve_data.cve_id,
        description=cve_data.description,
        severity=cve_data.severity,
        ecosystem=cve_data.ecosystem,
        published_date=str(cve_data.published_date),
        last_modified_date=str(cve_data.last_modified_date),
        cvss_score=cve_data.cvss_score,
        mcp_server_count=cve_data.mcp_server_count,
        mcp_llm_axis_score=cve_data.mcp_llm_axis_score if cve_data.mcp_llm_axis_score is not None else 0.0,
        dispute_count=cve_data.dispute_count,
        org_count=cve_data.org_count
    )

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(
            cve_id="CVE-2021-1234",
            description="Test CVE 1",
            severity="High",
            ecosystem="Linux",
            published_date="2021-01-01",
            last_modified_date="2021-01-02",
            cvss_score=7.5,
            org_id=1
        ),
        MCPServerRegistry(
            cve_id="CVE-2021-5678",
            description="Test CVE 2",
            severity="Medium",
            ecosystem="Windows",
            published_date="2021-02-01",
            last_modified_date="2021-02-02",
            cvss_score=5.0,
            org_id=2
        ),
        MCPLLMAxisScores(
            cve_id="CVE-2021-1234",
            score=0.8
        ),
        MCPLLMAxisScores(
            cve_id="CVE-2021-5678",
            score=0.6
        ),
        MCPScoreDisputes(
            cve_id="CVE-2021-1234"
        ),
        Orgs(
            id=1,
            name="Test Org 1"
        ),
        Orgs(
            id=2,
            name="Test Org 2"
        )
    ])
    test_session.commit()

    # Test the endpoints
    uvicorn.run(app, host="127.0.0.1", port=8000)

    # Verify the test data
    test_cves = get_cve_data(test_session)
    if len(test_cves) == 2:
        print("PASS")
    else:
        print("FAIL")