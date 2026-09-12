from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from app.db import get_session
from .logic import propagate_family_threats

router = APIRouter(prefix="/api/cve", tags=["cve"])

class FamilyPropagationRequest(BaseModel):
    advisory_ids: List[str]

class FamilyPropagationResponse(BaseModel):
    propagated: int
    errors: List[str]

@router.post("/family_propagation", response_model=FamilyPropagationResponse)
async def family_propagation(
    request: FamilyPropagationRequest,
    session = Depends(get_session)
):
    return propagate_family_threats(session, request.advisory_ids)

if __name__ == "__main__":
    import sqlalchemy as sa
    from sqlalchemy.orm import Session
    from app.db import get_session
    from app import app

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()

    sa.Table("vuln_advisories", metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("cve_id", sa.String),
        sa.Column("severity", sa.String),
        sa.Column("description", sa.String),
    )
    sa.Table("vuln_links", metadata,
        sa.Column("source_cve_id", sa.String),
        sa.Column("target_cve_id", sa.String),
        sa.Column("relationship", sa.String),
    )
    sa.Table("mcp_threat_associations", metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("advisory_id", sa.String),
        sa.Column("cve_id", sa.String),
        sa.Column("threat_type", sa.String),
        sa.Column("severity", sa.String),
    )
    metadata.create_all(engine)
    testing_session = Session(bind=engine)

    app.dependency_overrides[get_session] = lambda: testing_session

    from sqlalchemy import text
    from .logic import propagate_family_threats

    testing_session.execute(text("""
        INSERT INTO vuln_advisories (id, cve_id, severity, description)
        VALUES
            ('adv-001', 'CVE-2023-2001', 'CRITICAL', 'SQL Injection in login module'),
            ('adv-002', 'CVE-2023-2002', 'HIGH', 'Buffer overflow in legacy module'),
            ('adv-003', 'CVE-2023-2003', 'MEDIUM', 'XSS in admin interface')
    """))
    testing_session.execute(text("""
        INSERT INTO vuln_links (source_cve_id, target_cve_id, relationship)
        VALUES
            ('CVE-2023-2001', 'CVE-2023-2002', 'related'),
            ('CVE-2023-2002', 'CVE-2023-2003', 'related')
    """))
    testing_session.commit()

    result = propagate_family_threats(testing_session, ["adv-001"])
    assert result["propagated"] == 3, f"Expected 3, got {result['propagated']}"
    print("PASS")