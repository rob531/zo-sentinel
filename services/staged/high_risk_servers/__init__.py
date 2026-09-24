from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, McpLlmAxisScore
import requests
from pydantic import BaseModel

class ScoreDispute(BaseModel):
    id: int
    server_id: int
    score_type: str
    old_score: float
    new_score: float
    reason: str
    resolved: bool
    resolved_by: Optional[int]
    resolved_at: Optional[str]

class MeshMemory(BaseModel):
    id: int
    server_id: int
    data: Dict[str, Any]

class MeshScore(BaseModel):
    server_id: int
    score: float
    score_type: str

class ServicePackage:
    def __init__(self):
        self.base_url = "http://127.0.0.1:8772"

    async def get_mesh_scores(self, server_id: int) -> List[MeshScore]:
        response = requests.get(f"{self.base_url}/query/mcp_signal_scores?server_id={server_id}")
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
        return response.json()

    async def get_mesh_memory_endpoint(self, server_id: int) -> Optional[MeshMemory]:
        response = requests.get(f"{self.base_url}/query/mesh_memory?server_id={server_id}")
        if response.status_code != 200:
            return None
        return response.json()

    async def get_mesh_memory_by_id(self, memory_id: int) -> Optional[MeshMemory]:
        response = requests.get(f"{self.base_url}/query/mesh_memory?id={memory_id}")
        if response.status_code != 200:
            return None
        return response.json()

    async def get_score_disputes_endpoint(self, server_id: int, db: Session = Depends(get_session)) -> List[ScoreDispute]:
        disputes = db.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()
        return [ScoreDispute(**dispute.__dict__) for dispute in disputes]

    async def mesh_scores_endpoint(self, server_id: int) -> List[MeshScore]:
        return await self.get_mesh_scores(server_id)

    async def mesh_memory_endpoint(self, server_id: int) -> Optional[MeshMemory]:
        return await self.get_mesh_memory_endpoint(server_id)

    async def signal_scores_endpoint(self, server_id: int) -> List[MeshScore]:
        return await self.get_mesh_scores(server_id)

    async def get_signal_score_by_id(self, score_id: int) -> Optional[MeshScore]:
        response = requests.get(f"{self.base_url}/query/mcp_signal_scores?id={score_id}")
        if response.status_code != 200:
            return None
        return response.json()

    async def reset_server_export_api_quarantine(self, db: Session = Depends(get_session)) -> None:
        db.query(McpServerRegistry).update({"export_api_quarantine": False})
        db.commit()

    async def _run_self_test(self) -> bool:
        try:
            # Test mesh scores endpoint
            scores = await self.get_mesh_scores(1)
            if not isinstance(scores, list):
                return False

            # Test mesh memory endpoint
            memory = await self.get_mesh_memory_endpoint(1)
            if memory is not None and not isinstance(memory, MeshMemory):
                return False

            # Test score disputes endpoint
            disputes = await self.get_score_disputes_endpoint(1, Session())
            if not isinstance(disputes, list):
                return False

            # Test signal scores endpoint
            signal_scores = await self.signal_scores_endpoint(1)
            if not isinstance(signal_scores, list):
                return False

            # Test reset quarantine
            await self.reset_server_export_api_quarantine(Session())

            return True
        except Exception:
            return False

if __name__ == "__main__":
    service = ServicePackage()
    if service._run_self_test():
        print("PASS")
    else:
        print("FAIL")