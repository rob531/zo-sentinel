from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime
from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter(prefix="/api/vuln")

class Advisory(BaseModel):
    id: str
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: str
    fetched_at: str

@router.get("/advisories", response_model=List[Advisory])
async def list_advisories(limit: int = 100, offset: int = 0, session=Depends(get_session)):
    if limit > 500:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 500")

    advisories = session.query(VulnAdvisory).order_by(VulnAdvisory.published_at.desc()).limit(limit).offset(offset).all()

    return [
        Advisory(
            id=str(adv.id),
            feed=adv.feed,
            summary=adv.summary,
            severity=adv.severity,
            ecosystem=adv.ecosystem,
            package=adv.package,
            published_at=adv.published_at.isoformat(),
            fetched_at=adv.fetched_at.isoformat()
        )
        for adv in advisories
    ]

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    session = SessionLocal()
    session.execute("INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   ("1", "nvd", "Critical vulnerability in package A", "critical", "npm", "package-a", datetime(2023, 1, 1), datetime(2023, 1, 2)))
    session.execute("INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   ("2", "nvd", "High vulnerability in package B", "high", "npm", "package-b", datetime(2023, 1, 3), datetime(2023, 1, 4)))
    session.execute("INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   ("3", "nvd", "Medium vulnerability in package C", "medium", "npm", "package-c", datetime(2023, 1, 5), datetime(2023, 1, 6)))
    session.commit()

    client = TestClient(app)
    response = client.get("/api/vuln/advisories?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert "id" in response.json()[0]
    assert "feed" in response.json()[0]
    assert "summary" in response.json()[0]
    assert "severity" in response.json()[0]
    assert "ecosystem" in response.json()[0]
    assert "package" in response.json()[0]
    assert "published_at" in response.json()[0]
    assert "fetched_at" in response.json()[0]

    print("PASS")