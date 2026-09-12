from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from pydantic import BaseModel
from typing import List, Optional

class VulnAdvisoryCreate(BaseModel):
    cve_id: str
    summary: str
    affected_ranges: str

class VulnAdvisoryUpdate(BaseModel):
    summary: Optional[str] = None
    affected_ranges: Optional[str] = None

class VulnLinkCreate(BaseModel):
    advisory_id: int
    url: str

class VulnLinkUpdate(BaseModel):
    url: Optional[str] = None

def create_vuln_advisory(advisory: VulnAdvisoryCreate, db: Session = Depends(get_session)):
    db_advisory = VulnAdvisory(**advisory.dict())
    db.add(db_advisory)
    db.commit()
    db.refresh(db_advisory)
    return db_advisory

def get_vuln_advisory(advisory_id: int, db: Session = Depends(get_session)):
    db_advisory = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if db_advisory is None:
        raise HTTPException(status_code=404, detail="Advisory not found")
    return db_advisory

def get_vuln_advisories(db: Session = Depends(get_session)):
    return db.query(VulnAdvisory).all()

def update_vuln_advisory(advisory_id: int, advisory: VulnAdvisoryUpdate, db: Session = Depends(get_session)):
    db_advisory = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if db_advisory is None:
        raise HTTPException(status_code=404, detail="Advisory not found")
    for key, value in advisory.dict(exclude_unset=True).items():
        setattr(db_advisory, key, value)
    db.commit()
    db.refresh(db_advisory)
    return db_advisory

def delete_vuln_advisory(advisory_id: int, db: Session = Depends(get_session)):
    db_advisory = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if db_advisory is None:
        raise HTTPException(status_code=404, detail="Advisory not found")
    db.delete(db_advisory)
    db.commit()
    return {"message": "Advisory deleted successfully"}

def create_vuln_link(link: VulnLinkCreate, db: Session = Depends(get_session)):
    db_link = VulnLink(**link.dict())
    db.add(db_link)
    db.commit()
    db.refresh(db_link)
    return db_link

def get_vuln_link(link_id: int, db: Session = Depends(get_session)):
    db_link = db.query(VulnLink).filter(VulnLink.id == link_id).first()
    if db_link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    return db_link

def get_vuln_links(db: Session = Depends(get_session)):
    return db.query(VulnLink).all()

def update_vuln_link(link_id: int, link: VulnLinkUpdate, db: Session = Depends(get_session)):
    db_link = db.query(VulnLink).filter(VulnLink.id == link_id).first()
    if db_link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    for key, value in link.dict(exclude_unset=True).items():
        setattr(db_link, key, value)
    db.commit()
    db.refresh(db_link)
    return db_link

def delete_vuln_link(link_id: int, db: Session = Depends(get_session)):
    db_link = db.query(VulnLink).filter(VulnLink.id == link_id).first()
    if db_link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(db_link)
    db.commit()
    return {"message": "Link deleted successfully"}

if __name__ == "__main__":
    import pytest
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    from app.main import app
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    def test_create_vuln_advisory():
        response = client.post("/api/vuln/advisories", json={"cve_id": "CVE-2023-1234", "summary": "Test advisory", "affected_ranges": "1.0.0-2.0.0"})
        assert response.status_code == 200
        assert response.json()["cve_id"] == "CVE-2023-1234"

    def test_get_vuln_advisory():
        response = client.get("/api/vuln/advisories/1")
        assert response.status_code == 200
        assert response.json()["cve_id"] == "CVE-2023-1234"

    def test_update_vuln_advisory():
        response = client.put("/api/vuln/advisories/1", json={"summary": "Updated advisory"})
        assert response.status_code == 200
        assert response.json()["summary"] == "Updated advisory"

    def test_delete_vuln_advisory():
        response = client.delete("/api/vuln/advisories/1")
        assert response.status_code == 200
        assert response.json()["message"] == "Advisory deleted successfully"

    def test_create_vuln_link():
        response = client.post("/api/vuln/links", json={"advisory_id": 1, "url": "http://example.com"})
        assert response.status_code == 200
        assert response.json()["url"] == "http://example.com"

    def test_get_vuln_link():
        response = client.get("/api/vuln/links/1")
        assert response.status_code == 200
        assert response.json()["url"] == "http://example.com"

    def test_update_vuln_link():
        response = client.put("/api/vuln/links/1", json={"url": "http://updated.com"})
        assert response.status_code == 200
        assert response.json()["url"] == "http://updated.com"

    def test_delete_vuln_link():
        response = client.delete("/api/vuln/links/1")
        assert response.status_code == 200
        assert response.json()["message"] == "Link deleted successfully"

    print("PASS")