from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPFacet, MCPFacetType, MCPFacetValue
from typing import List, Dict, Any
from pydantic import BaseModel

app = FastAPI()

class FacetCompilationResponse(BaseModel):
    facets: List[Dict[str, Any]]

def compile_facets(db: Session = Depends(get_session)) -> FacetCompilationResponse:
    facets = db.query(MCPFacet).all()

    compiled_facets = []
    for facet in facets:
        facet_values = db.query(MCPFacetValue).filter(MCPFacetValue.facet_id == facet.id).all()
        compiled_facet = {
            "id": facet.id,
            "name": facet.name,
            "type": facet.type.value,
            "values": [{"id": value.id, "value": value.value} for value in facet_values]
        }
        compiled_facets.append(compiled_facet)

    return FacetCompilationResponse(facets=compiled_facets)

@app.get("/compile-facets", response_model=FacetCompilationResponse)
def get_compiled_facets(db: Session = Depends(get_session)):
    return compile_facets(db)

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import MCPFacet, MCPFacetType, MCPFacetValue
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_session = SessionLocal()
    test_facet = MCPFacet(name="has_known_cve", type=MCPFacetType.BOOLEAN)
    test_session.add(test_facet)
    test_session.commit()

    test_facet_value = MCPFacetValue(facet_id=test_facet.id, value="true")
    test_session.add(test_facet_value)
    test_session.commit()

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/compile-facets")
    assert response.status_code == 200
    response_data = response.json()
    assert any(facet["name"] == "has_known_cve" for facet in response_data["facets"])

    print("PASS")