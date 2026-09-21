"""
Service promotion readiness report contract.
Checks staged services for promotion readiness.
"""
import os
import sys
import json
import re
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional
from contextlib import contextmanager

from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User


class ServiceCheckResult(BaseModel):
    """Result of checking a single staged service."""
    name: str
    toml_ok: bool = False
    router_ok: bool = False
    contract_ok: bool = False
    init_ok: bool = False
    can_promote: bool = False


class ServicePromotionReadinessReport(BaseModel):
    """Report of all staged services promotion readiness."""
    staged_services: List[ServiceCheckResult]


def _check_toml_valid(path: Path) -> bool:
    """Check if service.toml exists and has valid route/port."""
    toml_path = path / "service.toml"
    if not toml_path.exists():
        return False
    try:
        content = toml_path.read_text()
        has_route = bool(re.search(r'route\s*=\s*["\']/?[\w\-/]+["\']', content))
        has_port = bool(re.search(r'port\s*=\s*\d+', content))
        return has_route and has_port
    except Exception:
        return False


def _check_router_valid(path: Path) -> bool:
    """Check if router.py exists and has at least one route."""
    router_path = path / "router.py"
    if not router_path.exists():
        return False
    try:
        content = router_path.read_text()
        has_router = "@router" in content or "APIRouter" in content
        has_route_decorator = "@" in content and ("get" in content or "post" in content or "put" in content or "delete" in content)
        return has_router and has_route_decorator
    except Exception:
        return False


def _check_contract_valid(path: Path) -> bool:
    """Check if contract.py exists and has pydantic models."""
    contract_path = path / "contract.py"
    if not contract_path.exists():
        return False
    try:
        content = contract_path.read_text()
        has_pydantic = "BaseModel" in content
        has_model = "class " in content and "Model" in content
        return has_pydantic and has_model
    except Exception:
        return False


def _check_init_valid(path: Path) -> bool:
    """Check if __init__.py exists and exports the router."""
    init_path = path / "__init__.py"
    if not init_path.exists():
        return False
    try:
        content = init_path.read_text()
        exports_router = "router" in content.lower()
        return exports_router
    except Exception:
        return False


def get_service_promotion_readiness(staged_base: str) -> ServicePromotionReadinessReport:
    """
    Scan all staged services and check promotion readiness.
    
    Args:
        staged_base: Base path to services/staged/ directory
        
    Returns:
        ServicePromotionReadinessReport with results for each service
    """
    staged_services = []
    staged_path = Path(staged_base)
    
    if not staged_path.exists():
        return ServicePromotionReadinessReport(staged_services=[])
    
    for service_dir in staged_path.iterdir():
        if not service_dir.is_dir():
            continue
        if service_dir.name.startswith("_") or service_dir.name.startswith("."):
            continue
            
        toml_ok = _check_toml_valid(service_dir)
        router_ok = _check_router_valid(service_dir)
        contract_ok = _check_contract_valid(service_dir)
        init_ok = _check_init_valid(service_dir)
        can_promote = toml_ok and router_ok and contract_ok and init_ok
        
        staged_services.append(ServiceCheckResult(
            name=service_dir.name,
            toml_ok=toml_ok,
            router_ok=router_ok,
            contract_ok=contract_ok,
            init_ok=init_ok,
            can_promote=can_promote
        ))
    
    return ServicePromotionReadinessReport(staged_services=staged_services)


def consume_server(server_name: str, session):
    """Consume server data - required by graph dependencies."""
    return {"server": server_name, "status": "consumed"}


if __name__ == "__main__":
    import tempfile
    import shutil
    from pathlib import Path
    
    # Create a temporary staged directory structure for testing
    test_dir = tempfile.mkdtemp()
    staged_path = Path(test_dir) / "staged"
    staged_path.mkdir()
    
    try:
        # Create a VALID service (has all required files)
        valid_service = staged_path / "valid_test_service"
        valid_service.mkdir()
        
        # service.toml with route and port
        (valid_service / "service.toml").write_text('''
[service]
name = "valid_test_service"
route = "/api/valid"
port = 8001
''')
        
        # router.py with a route
        (valid_service / "router.py").write_text('''
from fastapi import APIRouter

router = APIRouter()

@router.get("/test")
def test_endpoint():
    return {"status": "ok"}
''')
        
        # contract.py with pydantic models
        (valid_service / "contract.py").write_text('''
from pydantic import BaseModel

class TestModel(BaseModel):
    id: int
    name: str
''')
        
        # __init__.py that exports router
        (valid_service / "__init__.py").write_text('''
from .router import router

__all__ = ["router"]
''')
        
        # Create a BROKEN service (missing contract.py)
        broken_service = staged_path / "broken_test_service"
        broken_service.mkdir()
        
        # service.toml with route and port
        (broken_service / "service.toml").write_text('''
[service]
name = "broken_test_service"
route = "/api/broken"
port = 8002
''')
        
        # router.py with a route
        (broken_service / "router.py").write_text('''
from fastapi import APIRouter

router = APIRouter()

@router.get("/test")
def test_endpoint():
    return {"status": "ok"}
''')
        
        # MISSING contract.py - this is the breaking point
        
        # __init__.py that exports router
        (broken_service / "__init__.py").write_text('''
from .router import router

__all__ = ["router"]
''')
        
        # Run the check
        report = get_service_promotion_readiness(str(staged_path))
        
        # Find results for our test services
        valid_result = None
        broken_result = None
        for svc in report.staged_services:
            if svc.name == "valid_test_service":
                valid_result = svc
            elif svc.name == "broken_test_service":
                broken_result = svc
        
        # Validate results
        assert valid_result is not None, "valid_test_service not found in report"
        assert broken_result is not None, "broken_test_service not found in report"
        
        assert valid_result.can_promote == True, f"valid_test_service should can_promote=True, got {valid_result}"
        assert broken_result.can_promote == False, f"broken_test_service should can_promote=False, got {broken_result}"
        assert valid_result.contract_ok == True, "valid_test_service contract_ok should be True"
        assert broken_result.contract_ok == False, "broken_test_service contract_ok should be False"
        
        print("PASS")
        
    finally:
        # Cleanup
        shutil.rmtree(test_dir)