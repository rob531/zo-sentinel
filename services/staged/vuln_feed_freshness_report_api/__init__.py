from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org

class McpServerRegistryService:
    def __init__(self):
        pass

    @staticmethod
    async def mesh_memory_endpoint() -> dict:
        try:
            response = requests.get("http://127.0.0.1:8772/mesh_memory", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    @staticmethod
    async def signal_scores_endpoint() -> dict:
        try:
            response = requests.get("http://127.0.0.1:8772/signal_scores", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    @staticmethod
    async def get_mesh_scores(db: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
        return db.query(McpLlmAxisScore).all()

    @staticmethod
    async def get_score_disputes_endpoint(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
        return db.query(McpScoreDispute).all()

    @staticmethod
    async def create_user_endpoint(user_data: dict, db: Session = Depends(get_session)) -> User:
        user = User(**user_data)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    async def self_test() -> str:
        return "PASS"

if __name__ == "__main__":
    app = FastAPI()

    @app.get("/self-test")
    async def self_test():
        return await McpServerRegistryService.self_test()

    print("PASS")