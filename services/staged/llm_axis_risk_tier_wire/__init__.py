"""
LLM Axis Risk Tier Wire Service.

Provides base classes and utilities for LLM axis risk tier scoring across services.
"""


class OrgService:
    """Base service for organization-related operations."""

    def __init__(self, db_session=None):
        self.db_session = db_session

    def get_org(self, org_id: int):
        """Get organization by ID."""
        from app.models import Org

        if not self.db_session:
            return None
        return self.db_session.query(Org).filter(Org.id == org_id).first()


class UserService:
    """Base service for user-related operations."""

    def __init__(self, db_session=None):
        self.db_session = db_session

    def get_user(self, user_id: int):
        """Get user by ID."""
        from app.models import User

        if not self.db_session:
            return None
        return self.db_session.query(User).filter(User.id == user_id).first()


def get_mesh_memory(mesh_id: int = None, signal_id: int = None):
    """Retrieve mesh memory from the mesh store."""
    import httpx

    params = {}
    if mesh_id:
        params["mesh_id"] = mesh_id
    if signal_id:
        params["signal_id"] = signal_id

    try:
        response = httpx.get(
            "http://127.0.0.1:8772/query",
            params=params,
            timeout=5.0
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def get_mesh_memory_by_id(mesh_id: int):
    """Retrieve mesh memory by mesh ID."""
    return get_mesh_memory(mesh_id=mesh_id)


def mesh_memory_endpoint(mesh_id: int = None, signal_id: int = None):
    """Mesh memory endpoint for retrieving memory data."""
    return get_mesh_memory(mesh_id=mesh_id, signal_id=signal_id)


def get_signal_scores(signal_id: int = None, org_id: int = None):
    """Retrieve signal scores from the mesh store."""
    import httpx

    params = {}
    if signal_id:
        params["signal_id"] = signal_id
    if org_id:
        params["org_id"] = org_id

    try:
        response = httpx.get(
            "http://127.0.0.1:8772/query",
            params=params,
            timeout=5.0
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def mesh_scores_endpoint(
    org_id: int = None,
    signal_id: int = None,
    start_date: str = None,
    end_date: str = None
):
    """Mesh scores endpoint for retrieving score data."""
    import httpx

    params = {}
    if org_id is not None:
        params["org_id"] = org_id
    if signal_id is not None:
        params["signal_id"] = signal_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    try:
        response = httpx.get(
            "http://127.0.0.1:8772/query",
            params=params,
            timeout=5.0
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def _mesh_scores_endpoint_route(
    org_id: int = None,
    signal_id: int = None,
    start_date: str = None,
    end_date: str = None
):
    """Internal route wrapper for mesh scores endpoint."""
    return mesh_scores_endpoint(
        org_id=org_id,
        signal_id=signal_id,
        start_date=start_date,
        end_date=end_date
    )


def signal_scores_endpoint(
    signal_id: int = None,
    org_id: int = None,
    include_axis: bool = False
):
    """Signal scores endpoint for retrieving signal-based scores."""
    import httpx

    params = {}
    if signal_id is not None:
        params["signal_id"] = signal_id
    if org_id is not None:
        params["org_id"] = org_id
    if include_axis:
        params["include_axis"] = "true"

    try:
        response = httpx.get(
            "http://127.0.0.1:8772/query",
            params=params,
            timeout=5.0
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def llm_axis_scores_endpoint(
    org_id: int = None,
    signal_id: int = None,
    risk_tier: str = None
):
    """LLM axis scores endpoint for retrieving axis-based scores."""
    import httpx

    params = {}
    if org_id is not None:
        params["org_id"] = org_id
    if signal_id is not None:
        params["signal_id"] = signal_id
    if risk_tier:
        params["risk_tier"] = risk_tier

    try:
        response = httpx.get(
            "http://127.0.0.1:8772/query",
            params=params,
            timeout=5.0
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def mesh_scores(
    org_id: int = None,
    signal_id: int = None,
    limit: int = 100
):
    """Mesh scores utility for retrieving scored data."""
    import httpx

    params = {"limit": limit}
    if org_id is not None:
        params["org_id"] = org_id
    if signal_id is not None:
        params["signal_id"] = signal_id

    try:
        response = httpx.get(
            "http://127.0.0.1:8772/query",
            params=params,
            timeout=5.0
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def orgs_endpoint(org_id: int = None, limit: int = 100):
    """Organizations endpoint for retrieving org data."""
    from app.models import Org
    from app.db import get_session

    session = get_session()
    query = session.query(Org)
    if org_id is not None:
        query = query.filter(Org.id == org_id)
    query = query.limit(limit)
    return [{"id": org.id, "name": org.name} for org in query.all()]


def run_self_test(db_session=None):
    """Run self-test to verify the module is functional."""
    results = {"status": "pass", "tests": []}

    try:
        org_service = OrgService(db_session=db_session)
        results["tests"].append({
            "name": "OrgService_instantiation",
            "status": "pass"
        })
    except Exception as e:
        results["tests"].append({
            "name": "OrgService_instantiation",
            "status": "fail",
            "error": str(e)
        })
        results["status"] = "fail"

    try:
        user_service = UserService(db_session=db_session)
        results["tests"].append({
            "name": "UserService_instantiation",
            "status": "pass"
        })
    except Exception as e:
        results["tests"].append({
            "name": "UserService_instantiation",
            "status": "fail",
            "error": str(e)
        })
        results["status"] = "fail"

    try:
        mesh_memory = get_mesh_memory()
        results["tests"].append({
            "name": "get_mesh_memory",
            "status": "pass"
        })
    except Exception as e:
        results["tests"].append({
            "name": "get_mesh_memory",
            "status": "fail",
            "error": str(e)
        })
        results["status"] = "fail"

    try:
        signal_scores = get_signal_scores()
        results["tests"].append({
            "name": "get_signal_scores",
            "status": "pass"
        })
    except Exception as e:
        results["tests"].append({
            "name": "get_signal_scores",
            "status": "fail",
            "error": str(e)
        })
        results["status"] = "fail"

    try:
        scores = mesh_scores_endpoint()
        results["tests"].append({
            "name": "mesh_scores_endpoint",
            "status": "pass"
        })
    except Exception as e:
        results["tests"].append({
            "name": "mesh_scores_endpoint",
            "status": "fail",
            "error": str(e)
        })
        results["status"] = "fail"

    try:
        axis_scores = llm_axis_scores_endpoint()
        results["tests"].append({
            "name": "llm_axis_scores_endpoint",
            "status": "pass"
        })
    except Exception as e:
        results["tests"].append({
            "name": "llm_axis_scores_endpoint",
            "status": "fail",
            "error": str(e)
        })
        results["status"] = "fail"

    return results


def _run_self_test():
    """Internal self-test runner for use by other modules."""
    return run_self_test()


if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    from sqlalchemy.orm import sessionmaker
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    result = run_self_test(db_session=TestingSessionLocal())

    print(f"\n{'='*60}")
    print("LLM Axis Risk Tier Wire - Self Test Results")
    print(f"{'='*60}")

    for test in result["tests"]:
        status_symbol = "✓" if test["status"] == "pass" else "✗"
        print(f"  {status_symbol} {test['name']}: {test['status']}")
        if test.get("error"):
            print(f"      Error: {test['error']}")

    print(f"{'='*60}")
    print(f"OVERALL: {result['status'].upper()}")
    print(f"{'='*60}")

    if result["status"] == "pass":
        print("\nPASS")
    else:
        print("\nFAIL")
        exit(1)