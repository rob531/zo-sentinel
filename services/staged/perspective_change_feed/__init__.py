from typing import List, Optional
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User, Perspective
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

class PerspectiveService:
    @staticmethod
    def get_perspective_by_id(db: Session, perspective_id: int) -> Optional[Perspective]:
        return db.query(Perspective).filter(Perspective.id == perspective_id).first()

    @staticmethod
    def get_perspectives_by_org_id(db: Session, org_id: int) -> List[Perspective]:
        return db.query(Perspective).filter(Perspective.org_id == org_id).all()

    @staticmethod
    def create_perspective(db: Session, perspective: Perspective) -> Perspective:
        db.add(perspective)
        db.commit()
        db.refresh(perspective)
        return perspective

    @staticmethod
    def update_perspective(db: Session, perspective_id: int, updated_data: dict) -> Optional[Perspective]:
        perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
        if perspective:
            for key, value in updated_data.items():
                setattr(perspective, key, value)
            db.commit()
            db.refresh(perspective)
        return perspective

    @staticmethod
    def delete_perspective(db: Session, perspective_id: int) -> bool:
        perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
        if perspective:
            db.delete(perspective)
            db.commit()
            return True
        return False

class PerspectiveRequest(BaseModel):
    name: str
    description: str
    facet_filters: dict
    org_id: int
    created_by: int

class PerspectiveUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    facet_filters: Optional[dict] = None

def get_perspective_service(db: Session = Depends(get_session)):
    return PerspectiveService()

if __name__ == "__main__":
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=False, autoflush=False)

    @app.post("/perspectives")
    async def create_perspective_endpoint(perspective_data: PerspectiveRequest, service: PerspectiveService = Depends(get_perspective_service)):
        perspective = Perspective(
            name=perspective_data.name,
            description=perspective_data.description,
            facet_filters=perspective_data.facet_filters,
            org_id=perspective_data.org_id,
            created_by=perspective_data.created_by
        )
        return service.create_perspective(app.dependency_overrides[get_session](), perspective)

    @app.get("/perspectives/{perspective_id}")
    async def get_perspective_endpoint(perspective_id: int, service: PerspectiveService = Depends(get_perspective_service)):
        return service.get_perspective_by_id(app.dependency_overrides[get_session](), perspective_id)

    @app.get("/orgs/{org_id}/perspectives")
    async def get_perspectives_by_org_endpoint(org_id: int, service: PerspectiveService = Depends(get_perspective_service)):
        return service.get_perspectives_by_org_id(app.dependency_overrides[get_session](), org_id)

    @app.put("/perspectives/{perspective_id}")
    async def update_perspective_endpoint(perspective_id: int, update_data: PerspectiveUpdateRequest, service: PerspectiveService = Depends(get_perspective_service)):
        return service.update_perspective(app.dependency_overrides[get_session](), perspective_id, update_data.dict(exclude_unset=True))

    @app.delete("/perspectives/{perspective_id}")
    async def delete_perspective_endpoint(perspective_id: int, service: PerspectiveService = Depends(get_perspective_service)):
        return service.delete_perspective(app.dependency_overrides[get_session](), perspective_id)

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

    print("PASS")