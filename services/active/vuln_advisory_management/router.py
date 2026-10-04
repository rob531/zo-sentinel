from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users

router = APIRouter(
    prefix="/api",
    tags=["vuln_advisory_management"],
)

@router.get("/vuln_advisory_management")
async def get_vuln_advisory_management(db_session=Depends(get_session)):
    # Example endpoint to fetch data from the database
    servers = db_session.query(MCPServerRegistry).all()
    return {"servers": servers}

@router.post("/vuln_advisory_management")
async def create_vuln_advisory_management(data: dict, db_session=Depends(get_session)):
    # Example endpoint to create data in the database
    new_server = MCPServerRegistry(**data)
    db_session.add(new_server)
    db_session.commit()
    return {"message": "Server created successfully"}
