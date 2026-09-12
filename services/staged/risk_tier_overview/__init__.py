from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List
import requests

app = FastAPI()

def get_mesh_data(query: str) -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    response.raise_for_status()
    return response.json()

@app.get("/servers")
async def get_servers(db_session=Depends(get_session)):
    return db_session.query(McpServerRegistry).all()

@app.get("/scores")
async def get_scores(db_session=Depends(get_session)):
    return db_session.query(McpLlmAxisScore).all()

@app.get("/disputes")
async def get_disputes(db_session=Depends(get_session)):
    return db_session.query(McpScoreDispute).all()

@app.get("/orgs")
async def get_orgs(db_session=Depends(get_session)):
    return db_session.query(Org).all()

@app.get("/users")
async def get_users(db_session=Depends(get_session)):
    return db_session.query(User).all()

@app.get("/mesh_scores")
async def get_mesh_scores():
    return get_mesh_data("SELECT * FROM mcp_signal_scores")

@app.get("/mesh_memory")
async def get_mesh_memory():
    return get_mesh_data("SELECT * FROM mesh_memory")

if __name__ == "__main__":
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

    print("PASS")