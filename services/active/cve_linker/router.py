from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import CVE, ScoreDisputes
from sqlalchemy.orm import Session
import requests

router = APIRouter()

@router.get("/cves/")
async def get_cves(session: Session = Depends(get_session)):
    try:
        cves = session.query(CVE).all()
        return cves
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cves/")
async def create_cve(cve: CVE, session: Session = Depends(get_session)):
    try:
        session.add(cve)
        session.commit()
        session.refresh(cve)
        return cve
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/servers/")
async def get_servers(session: Session = Depends(get_session)):
    try:
        servers = session.query(Server).all()
        return servers
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scores/{server_id}")
async def get_scores(server_id: int, session: Session = Depends(get_session)):
    try:
        scores = session.query(Score).filter(Score.server_id == server_id).all()
        return scores
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/disputes/")
async def create_dispute(dispute: ScoreDisputes, session: Session = Depends(get_session)):
    try:
        session.add(dispute)
        session.commit()
        session.refresh(dispute)
        return dispute
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/orgs/")
async def get_orgs(session: Session = Depends(get_session)):
    try:
        orgs = session.query(Org).all()
        return orgs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users/")
async def get_users(session: Session = Depends(get_session)):
    try:
        users = session.query(User).all()
        return users
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mesh/query/")
async def query_mesh(query: dict):
    try:
        response = requests.post("http://127.0.0.1:8772/query", json=query)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mesh/execute/")
async def execute_mesh(execute: dict):
    try:
        response = requests.post("http://127.0.0.1:8772/execute", json=execute)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
