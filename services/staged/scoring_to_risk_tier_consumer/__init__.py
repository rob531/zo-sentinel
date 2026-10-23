from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List
import requests

app = FastAPI()

@app.get("/servers")
async def get_servers(db=Depends(get_session)):
    return db.query(McpServerRegistry).all()

@app.get("/scores")
async def get_scores(db=Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

@app.get("/disputes")
async def get_disputes(db=Depends(get_session)):
    return db.query(McpScoreDispute).all()

@app.get("/orgs")
async def get_orgs(db=Depends(get_session)):
    return db.query(Org).all()

@app.get("/users")
async def get_users(db=Depends(get_session)):
    return db.query(User).all()

@app.get("/mesh_scores")
async def get_mesh_scores():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

@app.get("/mesh_memory")
async def get_mesh_memory():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

if __name__ == "__main__":
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

    print("PASS")