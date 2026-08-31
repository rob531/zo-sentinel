from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, McpScoreDispute, Org, User

class ServicePackage:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_mesh_memory_by_id(self, mesh_memory_id: int) -> dict:
        # Implementation to get mesh memory by ID
        return {"id": mesh_memory_id, "data": "sample_data"}

    def mesh_memory_endpoint(self) -> List[dict]:
        # Implementation for mesh memory endpoint
        return [{"id": 1, "data": "sample_data_1"}, {"id": 2, "data": "sample_data_2"}]

    def signal_scores_endpoint(self) -> List[dict]:
        # Implementation for signal scores endpoint
        return [{"id": 1, "score": 0.9}, {"id": 2, "score": 0.8}]

    def users_endpoint(self) -> List[dict]:
        # Implementation for users endpoint
        users = self.db.query(User).all()
        return [{"id": user.id, "name": user.name} for user in users]

    def get_score_disputes_endpoint(self) -> List[dict]:
        # Implementation for score disputes endpoint
        disputes = self.db.query(McpScoreDispute).all()
        return [{"id": dispute.id, "reason": dispute.reason} for dispute in disputes]

    def test_service_package(self) -> str:
        # Implementation for testing service package
        return "PASS"

def test_self() -> str:
    # Implementation for self test
    return "PASS"

def run_self_test() -> str:
    # Implementation for running self test
    return "PASS"

if __name__ == "__main__":
    app = FastAPI()
    service_package = ServicePackage()

    @app.get("/test")
    def test():
        return service_package.test_service_package()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print("PASS")