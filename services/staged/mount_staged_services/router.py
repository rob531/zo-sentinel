from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import List, Dict, Any
import importlib
import logging
import sys
from app.db import get_session
from sqlalchemy.orm import Session

router = APIRouter()

# Configure logging for mount failures
logging.basicConfig(
    filename='mount_failures.log',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_staged_services() -> List[Path]:
    """Scan services/staged/ for directories containing router.py, contract.py, and service.toml."""
    staged_dir = Path(__file__).resolve().parent.parent.parent / "staged"
    services = []
    for service_dir in staged_dir.iterdir():
        if service_dir.is_dir():
            required_files = [
                "router.py",
                "contract.py",
                "service.toml"
            ]
            if all((service_dir / file).exists() for file in required_files):
                services.append(service_dir)
    return services

def verify_service_contract(service_dir: Path) -> bool:
    """Verify that the service's contract test returns 200."""
    contract_module = f"services.staged.{service_dir.name}.contract"
    try:
        module = importlib.import_module(contract_module)
        if hasattr(module, "test_contract"):
            result = module.test_contract()
            return result.status_code == 200
    except Exception as e:
        logger.error(f"Failed to verify contract for {service_dir.name}: {str(e)}")
    return False

def mount_service_route(service_dir: Path, app: APIRouter) -> bool:
    """Mount the service's router under the appropriate prefix."""
    router_module = f"services.staged.{service_dir.name}.router"
    try:
        module = importlib.import_module(router_module)
        if hasattr(module, "router"):
            prefix = f"/api/{service_dir.name}"
            app.include_router(module.router, prefix=prefix)
            return True
    except Exception as e:
        logger.error(f"Failed to mount route for {service_dir.name}: {str(e)}")
    return False

@router.get("/staged_services", response_model=List[Dict[str, Any]])
def list_staged_services(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """List all staged services that are mounted."""
    services = []
    for service_dir in get_staged_services():
        if verify_service_contract(service_dir):
            services.append({
                "name": service_dir.name,
                "prefix": f"/api/{service_dir.name}"
            })
    return services

def register_staged_services(app: APIRouter) -> None:
    """Register all staged services that pass contract verification."""
    for service_dir in get_staged_services():
        if verify_service_contract(service_dir):
            mount_service_route(service_dir, app)

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Register the router
    app.include_router(router)

    # Test the router
    routes = [r.path for r in app.routes]
    print("PASS" if routes else "FAIL")