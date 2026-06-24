import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typing import List, Dict, Any

# Mocking the database and consumers for testing
class MockDatabase:
    def __init__(self):
        self.data = {}

    def seed_data(self, data: Dict[str, List[Dict[str, Any]]]):
        self.data = data

    def get_all_entities(self):
        return self.data.get("entities", [])

    def get_entity_by_id(self, entity_id: str):
        for entity in self.data.get("entities", []):
            if entity.get("id") == entity_id:
                return entity
        return None

    def get_verdicts_for_entity(self, entity_id: str):
        return [v for v in self.data.get("verdicts", []) if v.get("entity_id") == entity_id]

    def get_scoring_data_for_entity(self, entity_id: str):
        return [s for s in self.data.get("scoring_data", []) if s.get("entity_id") == entity_id]

    def search_entities(self, query: str):
        results = []
        for entity in self.data.get("entities", []):
            if query.lower() in entity.get("name", "").lower() or query.lower() in entity.get("id", "").lower():
                results.append(entity)
        return results

    def get_dashboard_summary(self):
        entities = self.data.get("entities", [])
        verdicts = self.data.get("verdicts", [])
        return {
            "total_entities": len(entities),
            "total_verdicts": len(verdicts),
            "verdict_counts": {
                "positive": sum(1 for v in verdicts if v.get("verdict") == "positive"),
                "negative": sum(1 for v in verdicts if v.get("verdict") == "negative"),
                "neutral": sum(1 for v in verdicts if v.get("verdict") == "neutral"),
            }
        }

# Mocking consumer classes
class MockAppScoringConsumer:
    def __init__(self, db: MockDatabase):
        self.db = db

    def process_scoring(self, entity_id: str):
        # Simulate scoring process
        return {"entity_id": entity_id, "score": 0.85, "model_version": "1.0"}

class MockVerdictViewAPI:
    def __init__(self, db: MockDatabase):
        self.db = db

    def get_verdicts(self, entity_id: str):
        return self.db.get_verdicts_for_entity(entity_id)

class MockDashboardSummaryAPI:
    def __init__(self, db: MockDatabase):
        self.db = db

    def get_summary(self):
        return self.db.get_dashboard_summary()

class MockOverviewDashboardView:
    def __init__(self, db: MockDatabase):
        self.db = db

    def get_overview(self):
        return {
            "total_entities": len(self.db.get_all_entities()),
            "recent_activity": "Simulated recent activity data"
        }

class MockEntityDetailView:
    def __init__(self, db: MockDatabase):
        self.db = db

    def get_entity_details(self, entity_id: str):
        entity = self.db.get_entity_by_id(entity_id)
        if entity:
            return {
                "entity": entity,
                "verdicts": self.db.get_verdicts_for_entity(entity_id),
                "scoring_data": self.db.get_scoring_data_for_entity(entity_id)
            }
        return None

class MockRegistrySearchView:
    def __init__(self, db: MockDatabase):
        self.db = db

    def search(self, query: str):
        return self.db.search_entities(query)

# --- Routers ---
from fastapi import APIRouter, Depends

def get_db():
    # In a real app, this would be a dependency injection for the actual DB connection
    # For testing, we'll use a global mock DB instance
    return mock_db_instance

# App Scoring Consumer Router
app_scoring_consumer_router = APIRouter(prefix="/scoring", tags=["Scoring"])

@app_scoring_consumer_router.post("/process/{entity_id}")
def process_scoring(entity_id: str, db: MockDatabase = Depends(get_db)):
    consumer = MockAppScoringConsumer(db)
    return consumer.process_scoring(entity_id)

# Verdict View API Router
verdict_view_api_router = APIRouter(prefix="/verdicts", tags=["Verdicts"])

@verdict_view_api_router.get("/{entity_id}")
def get_verdicts(entity_id: str, db: MockDatabase = Depends(get_db)):
    api = MockVerdictViewAPI(db)
    return api.get_verdicts(entity_id)

# Dashboard Summary API Router
dashboard_summary_api_router = APIRouter(prefix="/dashboard/summary", tags=["Dashboard"])

@dashboard_summary_api_router.get("/")
def get_dashboard_summary(db: MockDatabase = Depends(get_db)):
    api = MockDashboardSummaryAPI(db)
    return api.get_summary()

# Overview Dashboard View Router
overview_dashboard_view_router = APIRouter(prefix="/dashboard/overview", tags=["Dashboard"])

@overview_dashboard_view_router.get("/")
def get_overview_dashboard(db: MockDatabase = Depends(get_db)):
    view = MockOverviewDashboardView(db)
    return view.get_overview()

# Entity Detail View Router
entity_detail_view_router = APIRouter(prefix="/entities", tags=["Entities"])

@entity_detail_view_router.get("/{entity_id}")
def get_entity_detail(entity_id: str, db: MockDatabase = Depends(get_db)):
    view = MockEntityDetailView(db)
    return view.get_entity_details(entity_id)

# Registry Search View Router
registry_search_view_router = APIRouter(prefix="/search", tags=["Search"])

@registry_search_view_router.get("/")
def search_registry(query: str, db: MockDatabase = Depends(get_db)):
    view = MockRegistrySearchView(db)
    return view.search(query)

# --- Main App Router Registry ---

def register_routers(app: FastAPI):
    """Registers all the routers for the app."""
    routers_to_register = [
        app_scoring_consumer_router,
        verdict_view_api_router,
        dashboard_summary_api_router,
        overview_dashboard_view_router,
        entity_detail_view_router,
        registry_search_view_router,
    ]
    for router in routers_to_register:
        app.include_router(router)
    return routers_to_register

# --- Self-Test ---

# Global mock database instance for testing
mock_db_instance = MockDatabase()

@pytest.fixture(scope="module")
def test_client():
    app = FastAPI()
    registered_routers = register_routers(app)

    # Seed the in-memory store
    mock_db_instance.seed_data({
        "entities": [
            {"id": "entity1", "name": "Example Entity 1", "type": "typeA"},
            {"id": "entity2", "name": "Another Entity", "type": "typeB"},
        ],
        "verdicts": [
            {"id": "v1", "entity_id": "entity1", "verdict": "positive", "timestamp": "2023-10-27T10:00:00Z"},
            {"id": "v2", "entity_id": "entity1", "verdict": "negative", "timestamp": "2023-10-27T11:00:00Z"},
            {"id": "v3", "entity_id": "entity2", "verdict": "neutral", "timestamp": "2023-10-27T12:00:00Z"},
        ],
        "scoring_data": [
            {"entity_id": "entity1", "score": 0.9, "model_version": "1.0"},
            {"entity_id": "entity2", "score": 0.5, "model_version": "1.1"},
        ]
    })

    client = TestClient(app)
    yield client, registered_routers

def test_all_routers_registered(test_client):
    client, registered_routers = test_client

    # Assert that the correct number of routers are registered
    assert len(client.app.router.routes) > 0, "No routes found in the application."

    # Check if specific endpoints are accessible, implying router registration
    # This is a more robust check than just counting routers, as it verifies functionality
    expected_endpoints = {
        "/scoring/process/entity1",
        "/verdicts/entity1",
        "/dashboard/summary/",
        "/dashboard/overview/",
        "/entities/entity1",
        "/search/?query=entity",
    }

    # Get all registered routes from the FastAPI app
    registered_paths = {route.path for route in client.app.routes}

    # Check if all expected endpoints are present
    for endpoint in expected_endpoints:
        assert endpoint in registered_paths, f"Endpoint {endpoint} not found. Router might not be registered correctly."

    # Optional: Check if the number of registered routers matches the expected list
    # Note: This might be less reliable if routers are nested or dynamically added in complex ways.
    # The endpoint check above is generally more practical.
    assert len(registered_routers) == 6, f"Expected 6 routers to be registered, but found {len(registered_routers)}."

    print("PASS")

if __name__ == "__main__":
    # This block is for running the test directly if pytest is not used
    # In a real project, you would typically run tests using `pytest` command
    print("Running self-test...")
    mock_db_instance.seed_data({
        "entities": [
            {"id": "entity1", "name": "Example Entity 1", "type": "typeA"},
            {"id": "entity2", "name": "Another Entity", "type": "typeB"},
        ],
        "verdicts": [
            {"id": "v1", "entity_id": "entity1", "verdict": "positive", "timestamp": "2023-10-27T10:00:00Z"},
            {"id": "v2", "entity_id": "entity1", "verdict": "negative", "timestamp": "2023-10-27T11:00:00Z"},
            {"id": "v3", "entity_id": "entity2", "verdict": "neutral", "timestamp": "2023-10-27T12:00:00Z"},
        ],
        "scoring_data": [
            {"entity_id": "entity1", "score": 0.9, "model_version": "1.0"},
            {"entity_id": "entity2", "score": 0.5, "model_version": "1.1"},
        ]
    })
    app = FastAPI()
    registered_routers = register_routers(app)
    client = TestClient(app)

    # Simulate the test logic
    try:
        # Assert that the correct number of routers are registered
        assert len(client.app.router.routes) > 0, "No routes found in the application."

        expected_endpoints = {
            "/scoring/process/entity1",
            "/verdicts/entity1",
            "/dashboard/summary/",
            "/dashboard/overview/",
            "/entities/entity1",
            "/search/?query=entity",
        }
        registered_paths = {route.path for route in client.app.routes}

        for endpoint in expected_endpoints:
            assert endpoint in registered_paths, f"Endpoint {endpoint} not found. Router might not be registered correctly."

        assert len(registered_routers) == 6, f"Expected 6 routers to be registered, but found {len(registered_routers)}."

        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")