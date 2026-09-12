from datetime import datetime, timedelta
from typing import List

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class VerdictCount(BaseModel):
    date: str
    verdict: str
    count: int


class VerdictDistributionResponse(BaseModel):
    days: int
    series: List[VerdictCount]


def compute_verdict_distribution(days: int, db: Session) -> VerdictDistributionResponse:
    start_date = datetime.utcnow().date() - timedelta(days=days - 1)
    
    query = (
        db.query(
            func.date(McpServerRegistry.last_assessed).label("date"),
            McpServerRegistry.verdict,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .filter(McpServerRegistry.last_assessed >= start_date)
        .group_by(func.date(McpServerRegistry.last_assessed), McpServerRegistry.verdict)
        .order_by(func.date(McpServerRegistry.last_assessed), McpServerRegistry.verdict)
    )
    
    results = query.all()
    
    series = [
        VerdictCount(date=str(r.date), verdict=r.verdict or "unknown", count=r.count)
        for r in results
    ]
    
    return VerdictDistributionResponse(days=days, series=series)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    
    session = TestSession()
    
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    
    servers = [
        McpServerRegistry(server_id="srv1", name="server1", verdict="clean", last_assessed=datetime.combine(yesterday, datetime.min.time())),
        McpServerRegistry(server_id="srv2", name="server2", verdict="clean", last_assessed=datetime.combine(yesterday, datetime.min.time())),
        McpServerRegistry(server_id="srv3", name="server3", verdict="malicious", last_assessed=datetime.combine(yesterday, datetime.min.time())),
        McpServerRegistry(server_id="srv4", name="server4", verdict="suspicious", last_assessed=datetime.combine(today, datetime.min.time())),
        McpServerRegistry(server_id="srv5", name="server5", verdict="clean", last_assessed=datetime.combine(today, datetime.min.time())),
    ]
    session.add_all(servers)
    session.commit()
    
    that_app = FastAPI()
    
    @that_app.get("/api/verdicts/distribution")
    def get_distribution(days: int = 7):
        result = compute_verdict_distribution(days, TestSession())
        return result.model_dump()
    
    with TestSession() as db:
        response = compute_verdict_distribution(days=2, db=db)
    
    assert len(response.series) >= 3, f"Expected at least 3 series entries, got {len(response.series)}"
    
    known_counts = [s for s in response.series if s.verdict == "clean" and s.count == 2]
    assert len(known_counts) > 0, f"Expected at least one entry with verdict=clean and count=2, got {response.series}"
    
    print("PASS")