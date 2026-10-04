# zo_sentinel/__init__.py
# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry as AppMcpServerRegistry


class SentinelBase(BaseModel):
    """Base class for sentinel domain models."""
    
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        from_attributes = True


class ServiceHealth(BaseModel):
    """Service health status model."""
    service_name: str
    status: str = "healthy"
    last_check: datetime = Field(default_factory=datetime.utcnow)
    details: Optional[Dict[str, Any]] = None


class DirectivePayload(BaseModel):
    """Sentinel directive payload."""
    directive_id: UUID
    action: str
    target: str
    params: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 0


class DirectiveResult(BaseModel):
    """Result of directive execution."""
    directive_id: UUID
    status: str
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    executed_at: datetime = Field(default_factory=datetime.utcnow)


def get_registry_by_name(session, name: str) -> Optional[AppMcpServerRegistry]:
    """Query MCP server registry by name."""
    return session.query(AppMcpServerRegistry).filter(
        AppMcpServerRegistry.name == name
    ).first()


def list_active_registries(session) -> List[AppMcpServerRegistry]:
    """List all active MCP server registries."""
    return session.query(AppMcpServerRegistry).filter(
        AppMcpServerRegistry.status == "active"
    ).all()


def create_health_check(service_name: str, status: str = "healthy") -> ServiceHealth:
    """Create a health check record."""
    return ServiceHealth(service_name=service_name, status=status)


__all__ = [
    "SentinelBase",
    "ServiceHealth",
    "DirectivePayload",
    "DirectiveResult",
    "get_registry_by_name",
    "list_active_registries",
    "create_health_check",
    "get_session",
    "AppMcpServerRegistry",
]


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    # In-memory self-test with StaticPool
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()
    
    # Test imports work
    assert SentinelBase is not None
    assert ServiceHealth is not None
    assert DirectivePayload is not None
    assert DirectiveResult is not None
    assert get_registry_by_name is not None
    assert list_active_registries is not None
    assert create_health_check is not None
    assert "get_session" in dir()
    assert "AppMcpServerRegistry" in dir()
    
    # Test model instantiation
    health = create_health_check("test_service", "healthy")
    assert health.service_name == "test_service"
    assert health.status == "healthy"
    
    directive = DirectivePayload(
        directive_id=uuid4(),
        action="test",
        target="test_target",
        params={"key": "value"}
    )
    assert directive.action == "test"
    
    result = DirectiveResult(
        directive_id=directive.directive_id,
        status="success"
    )
    assert result.status == "success"
    
    print("PASS")