# services/staged/server_registry_source_distribution_logic/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class SourceDistribution(BaseModel):
    source: str
    count: int


class SourceDistributionResponse(BaseModel):
    sources: list[SourceDistribution]


@router.get("/source-distribution", response_model=SourceDistributionResponse)
def get_source_distribution(session: Session = Depends(get_session)) -> SourceDistributionResponse:
    results = (
        session.query(McpServerRegistry.registry_source, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.registry_source)
        .all()
    )
    return SourceDistributionResponse(
        sources=[SourceDistribution(source=source, count=count) for source, count in results]
    )


def get_counts_from_session(session: Session) -> list[tuple[str, int]]:
    return (
        session.query(McpServerRegistry.registry_source, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.registry_source)
        .all()
    )