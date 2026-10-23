import logging
from pathlib import Path
from typing import List, Dict, Optional
import importlib.util
import sys
from fastapi import FastAPI, APIRouter, Depends
from fastapi.responses import JSONResponse
from app.db import get_session
from app.models import McpServerRegistry, Org, User
from sqlalchemy.orm import Session

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mount_failures.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def scan_staged_services() -> List[Dict[str, str]]:
    """Scan services/staged/ for valid service units with router.py, contract.py, and service.toml."""
    staged_dir = Path(__file__).parent.parent.parent / "staged"
    services = []

    for service_dir in staged_dir.iterdir():
        if not service_dir.is_dir():
            continue

        router_path = service_dir / "router.py"
        contract_path = service_dir / "contract.py"
        toml_path = service_dir / "service.toml"

        if router_path.exists() and contract_path.exists() and toml_path.exists():
            services.append({
                "name": service_dir.name,
                "path": str(service_dir),
                "router": str(router_path),
                "contract": str(contract_path),
                "toml": str(toml_path)
            })

    return services

def load_router(module_path: str) -> Optional[APIRouter]:
    """Dynamically load a router module from a given path."""
    try:
        spec = importlib.util.spec_from_file_location("router", module_path)
        if spec is None:
            logger.error(f"Failed to load spec for {module_path}")
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules["router"] = module
        spec.loader.exec_module(module)

        if hasattr(module, "router"):
            return module.router
        else:
            logger.error(f"No 'router' found in {module_path}")
            return None
    except Exception as e:
        logger.error(f"Error loading router from {module_path}: {str(e)}")
        return None

def mount_service_router(app: FastAPI, service_name: str, router: APIRouter, prefix: str) -> bool:
    """Mount a service router to the FastAPI app with error handling."""
    try:
        app.include_router(router, prefix=prefix, tags=[service_name])
        logger.info(f"Successfully mounted {service_name} at {prefix}")
        return True
    except Exception as e:
        logger.error(f"Failed to mount {service_name}: {str(e)}")
        return False

def verify_contract_test(service_path: str) -> bool:
    """Verify that the contract test for a service returns 200."""
    contract_path = f"{service_path}/contract.py"
    try:
        spec = importlib.util.spec_from_file_location("contract", contract_path)
        if spec is None:
            logger.error(f"Failed to load spec for {contract_path}")
            return False

        module = importlib.util.module_from_spec(spec)
        sys.modules["contract"] = module
        spec.loader.exec_module(module)

        if hasattr(module, "test_contract"):
            response = module.test_contract()
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Contract test for {service_path} returned {response.status_code}")
                return False
        else:
            logger.error(f"No 'test_contract' found in {contract_path}")
            return False
    except Exception as e:
        logger.error(f"Error verifying contract for {service_path}: {str(e)}")
        return False

def mount_staged_services(app: FastAPI) -> None:
    """Scan for staged services, verify their contract tests, and mount their routers."""
    services = scan_staged_services()

    for service in services:
        if not verify_contract_test(service["path"]):
            logger.error(f"Contract test failed for {service['name']}, skipping mount")
            continue

        router = load_router(service["router"])
        if router is None:
            continue

        prefix = f"/api/{service['name']}"
        mount_service_router(app, service["name"], router, prefix)

if __name__ == "__main__":
    from fastapi import FastAPI
    app = FastAPI()

    # Override the session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Mount services
    mount_staged_services(app)

    # Verify routes
    routes = [r.path for r in app.routes]
    print("PASS" if routes else "FAIL")