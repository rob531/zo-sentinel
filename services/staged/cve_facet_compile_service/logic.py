from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, select, distinct
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import List

from app.db import get_session
from app.models import Base, VulnAdvisory

app = FastAPI()


class FacetsResponse(BaseModel):
    facets: dict


@app.get("/api/cve/facet/compile", response_model=FacetsResponse)
def compile_facets(session: Session = Depends(get_session)):
    ecosystems = [r[0] for r in session.execute(select(distinct(VulnAdvisory.ecosystem))).all()]
    severities = [r[0] for r in session.execute(select(distinct(VulnAdvisory.severity))).all()]
    packages = [r[0] for r in session.execute(select(distinct(VulnAdvisory.package))).all()]
    return FacetsResponse(facets={"ecosystem": ecosystems, "severity": severities, "package": packages})


if __name__ == "__main__":
    from datetime import datetime
    from fastapi.testclient import TestClient

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(app.router)

    with TestClient(that_app) as client:
        session = TestingSessionLocal()
        session.add(VulnAdvisory(id=1, ecosystem="npm", severity="HIGH", package="lodash", feed="nvd",
                                 fetched_at=datetime.now(), published_at=datetime.now(),
                                 source_url="http://example.com/1", summary="adv1",
                                 affected_ranges="[]", aliases="[]", content_hash="h1", identities="{}"))
        session.add(VulnAdvisory(id=2, ecosystem="pypi", severity="MEDIUM", package="requests", feed="ghsa",
                                 fetched_at=datetime.now(), published_at=datetime.now(),
                                 source_url="http://example.com/2", summary="adv2",
                                 affected_ranges="[]", aliases="[]", content_hash="h2", identities="{}"))
        session.add(VulnAdvisory(id=3, ecosystem="npm", severity="CRITICAL", package="express", feed="nvd",
                                 fetched_at=datetime.now(), published_at=datetime.now(),
                                 source_url="http://example.com/3", summary="adv3",
                                 affected_ranges="[]", aliases="[]", content_hash="h3", identities="{}"))
        session.commit()
        session.close()

        that_app.dependency_overrides[get_session] = override_get_session

        response = client.get("/api/cve/facet/compile")
        assert response.status_code == 200
        data = response.json()
        assert len(data["facets"]["ecosystem"]) == 2
        assert len(data["facets"]["severity"]) == 3
        assert len(data["facets"]["package"]) == 3
        print("PASS")