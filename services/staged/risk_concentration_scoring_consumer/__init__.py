from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from typing import List, Optional
import requests

class McpServerRegistryService:
    def __init__(self):
        self.base_url = "http://127.0.0.1:8772"

    async def get_server_registry(self, db: Session = Depends(get_session)) -> List[McpServerRegistry]:
        try:
            response = requests.get(f"{self.base_url}/query", params={"table": "McpServerRegistry"}, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def get_server_by_id(self, server_id: int, db: Session = Depends(get_session)) -> Optional[McpServerRegistry]:
        try:
            response = requests.get(f"{self.base_url}/query", params={"table": "McpServerRegistry", "id": server_id}, timeout=10)
            response.raise_for_status()
            servers = response.json()
            return next((server for server in servers if server["id"] == server_id), None)
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def create_server(self, server_data: dict, db: Session = Depends(get_session)) -> McpServerRegistry:
        try:
            response = requests.post(f"{self.base_url}/query", json={"table": "McpServerRegistry", "data": server_data}, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def update_server(self, server_id: int, server_data: dict, db: Session = Depends(get_session)) -> McpServerRegistry:
        try:
            response = requests.put(f"{self.base_url}/query", json={"table": "McpServerRegistry", "id": server_id, "data": server_data}, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def delete_server(self, server_id: int, db: Session = Depends(get_session)) -> None:
        try:
            response = requests.delete(f"{self.base_url}/query", params={"table": "McpServerRegistry", "id": server_id}, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

def mesh_memory_endpoint():
    return McpServerRegistryService()

def get_mesh_memory_by_id(server_id: int):
    return McpServerRegistryService().get_server_by_id(server_id)

def test_self():
    service = McpServerRegistryService()
    try:
        servers = service.get_server_registry()
        if not servers:
            print("FAIL: No servers found")
            return
        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")

if __name__ == "__main__":
    test_self()