from typing import Any, Dict, List, Optional, Type, TypeVar
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
    VulnAdvisory,
)

T = TypeVar("T", bound=BaseModel)

class BaseService:
    """Base class for all services."""

    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

    def get_by_id(self, model: Type[T], id: UUID) -> Optional[T]:
        return self.session.query(model).get(id)

    def create(self, model: Type[T], data: Dict[str, Any]) -> T:
        instance = model(**data)
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def update(self, model: Type[T], id: UUID, data: Dict[str, Any]) -> T:
        instance = self.get_by_id(model, id)
        if not instance:
            raise HTTPException(status_code=404, detail="Not found")
        for key, value in data.items():
            setattr(instance, key, value)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def delete(self, model: Type[T], id: UUID) -> None:
        instance = self.get_by_id(model, id)
        if not instance:
            raise HTTPException(status_code=404, detail="Not found")
        self.session.delete(instance)
        self.session.commit()

class McpServerRegistryService(BaseService):
    """Service for McpServerRegistry model."""

    def get_by_server_id(self, server_id: str) -> Optional[McpServerRegistry]:
        return self.session.query(McpServerRegistry).filter_by(server_id=server_id).first()

class McpLlmAxisScoreService(BaseService):
    """Service for McpLlmAxisScore model."""

    def get_by_score_id(self, score_id: UUID) -> Optional[McpLlmAxisScore]:
        return self.session.query(McpLlmAxisScore).get(score_id)

class McpScoreDisputeService(BaseService):
    """Service for McpScoreDispute model."""

    def get_by_dispute_id(self, dispute_id: UUID) -> Optional[McpScoreDispute]:
        return self.session.query(McpScoreDispute).get(dispute_id)

class OrgService(BaseService):
    """Service for Org model."""

    def get_by_name(self, name: str) -> Optional[Org]:
        return self.session.query(Org).filter_by(name=name).first()

class UserService(BaseService):
    """Service for User model."""

    def get_by_email(self, email: str) -> Optional[User]:
        return self.session.query(User).filter_by(email=email).first()

class VulnAdvisoryService(BaseService):
    """Service for VulnAdvisory model."""

    def get_by_advisory_id(self, advisory_id: str) -> Optional[VulnAdvisory]:
        return self.session.query(VulnAdvisory).filter_by(advisory_id=advisory_id).first()

def get_mcp_server_registry_service() -> McpServerRegistryService:
    return McpServerRegistryService()

def get_mcp_llm_axis_score_service() -> McpLlmAxisScoreService:
    return McpLlmAxisScoreService()

def get_mcp_score_dispute_service() -> McpScoreDisputeService:
    return McpScoreDisputeService()

def get_org_service() -> OrgService:
    return OrgService()

def get_user_service() -> UserService:
    return UserService()

def get_vuln_advisory_service() -> VulnAdvisoryService:
    return VulnAdvisoryService()

if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    # Test setup
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=create_engine("sqlite:///:memory:", poolclass=StaticPool),
        expire_on_commit=False,
    )

    # Test services
    services = [
        get_mcp_server_registry_service(),
        get_mcp_llm_axis_score_service(),
        get_mcp_score_dispute_service(),
        get_org_service(),
        get_user_service(),
        get_vuln_advisory_service(),
    ]

    # Test CRUD operations
    try:
        for service in services:
            # Test create
            test_data = {"name": "test"}
            instance = service.create(service.__class__.__bases__[0].model, test_data)
            assert instance is not None

            # Test get_by_id
            retrieved = service.get_by_id(service.__class__.__bases__[0].model, instance.id)
            assert retrieved is not None

            # Test update
            update_data = {"name": "updated_test"}
            updated = service.update(service.__class__.__bases__[0].model, instance.id, update_data)
            assert updated.name == "updated_test"

            # Test delete
            service.delete(service.__class__.__bases__[0].model, instance.id)
            deleted = service.get_by_id(service.__class__.__bases__[0].model, instance.id)
            assert deleted is None

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")