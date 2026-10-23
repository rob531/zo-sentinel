from typing import List, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPRouterRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func
import inspect
import importlib
import os

class DeferredRouter(BaseModel):
    router_name: str
    declared_in: str
    last_modified: datetime

class DeferredRouterReport(BaseModel):
    deferred_routers: List[DeferredRouter]

def get_deferred_routers(db: Session) -> DeferredRouterReport:
    # Get all routers from the registry
    routers = db.query(MCPRouterRegistry).all()

    # Get mounted routers from main.py
    main_module = importlib.import_module("main")
    mounted_routers = []
    for name, obj in inspect.getmembers(main_module):
        if hasattr(obj, "prefix") and hasattr(obj, "tags"):
            mounted_routers.append(obj.prefix)

    # Find deferred routers
    deferred_routers = []
    for router in routers:
        if router.prefix not in mounted_routers:
            deferred_routers.append(DeferredRouter(
                router_name=router.prefix,
                declared_in=router.declared_in,
                last_modified=router.last_modified
            ))

    return DeferredRouterReport(deferred_routers=deferred_routers)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    test_routers = [
        MCPRouterRegistry(
            prefix="/api/routers/mounted1",
            declared_in="services/staged/router1.py",
            last_modified=datetime.now()
        ),
        MCPRouterRegistry(
            prefix="/api/routers/mounted2",
            declared_in="services/staged/router2.py",
            last_modified=datetime.now()
        ),
        MCPRouterRegistry(
            prefix="/api/routers/deferred1",
            declared_in="services/staged/router3.py",
            last_modified=datetime.now()
        )
    ]

    db = SessionLocal()
    db.add_all(test_routers)
    db.commit()

    # Mock main.py with only 2 mounted routers
    class MockMain:
        class Router1:
            prefix = "/api/routers/mounted1"
            tags = ["tag1"]

        class Router2:
            prefix = "/api/routers/mounted2"
            tags = ["tag2"]

    import sys
    sys.modules["main"] = MockMain

    # Test the function
    report = get_deferred_routers(db)
    assert report.deferred_routers is not None
    assert len(report.deferred_routers) == 1
    assert report.deferred_routers[0].router_name == "/api/routers/deferred1"

    print("PASS")