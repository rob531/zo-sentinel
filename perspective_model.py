from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Perspective
from typing import List, Dict, Any
import requests
import json

class PerspectiveModel:
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

    def create_perspective(self, org_id: int, name: str, description: str, facet_filters: Dict[str, Any], created_by: int):
        perspective = Perspective(
            org_id=org_id,
            name=name,
            description=description,
            facet_filters=json.dumps(facet_filters),
            created_by=created_by
        )
        self.session.add(perspective)
        self.session.commit()
        self.session.refresh(perspective)
        return perspective

    def get_perspective(self, perspective_id: int):
        return self.session.query(Perspective).filter(Perspective.id == perspective_id).first()

    def list_for_org(self, org_id: int):
        return self.session.query(Perspective).filter(Perspective.org_id == org_id).all()

    def update_perspective(self, perspective_id: int, name: str, description: str, facet_filters: Dict[str, Any]):
        perspective = self.get_perspective(perspective_id)
        if not perspective:
            raise HTTPException(status_code=404, detail="Perspective not found")
        perspective.name = name
        perspective.description = description
        perspective.facet_filters = json.dumps(facet_filters)
        self.session.commit()
        return perspective

    def delete_perspective(self, perspective_id: int):
        perspective = self.get_perspective(perspective_id)
        if not perspective:
            raise HTTPException(status_code=404, detail="Perspective not found")
        self.session.delete(perspective)
        self.session.commit()
        return {"message": "Perspective deleted successfully"}

    def validate_facet_filters(self, filters: Dict[str, Any], enums: Dict[str, List[Any]]):
        for key, value in filters.items():
            if key not in enums:
                raise ValueError(f"Unknown facet key: {key}")
            if isinstance(value, list):
                for item in value:
                    if item not in enums[key]:
                        raise ValueError(f"Unknown facet value: {item} for key: {key}")
            else:
                if value not in enums[key]:
                    raise ValueError(f"Unknown facet value: {value} for key: {key}")

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite database for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Override the dependency
    from app.dependency_overrides import app
    app.dependency_overrides[get_session] = override_get_session

    # Create a perspective model instance
    perspective_model = PerspectiveModel(SessionLocal())

    # Test data
    org_id = 1
    name = "Test Perspective"
    description = "This is a test perspective"
    facet_filters = {"status": ["active", "inactive"], "type": "user"}
    created_by = 1
    enums = {"status": ["active", "inactive"], "type": ["user", "admin"]}

    # Create a perspective
    perspective = perspective_model.create_perspective(org_id, name, description, facet_filters, created_by)

    # Validate a good filter
    try:
        perspective_model.validate_facet_filters(facet_filters, enums)
    except ValueError as e:
        print(f"Validation failed: {e}")
        exit(1)

    # Assert an unknown facet key is rejected
    bad_key_filters = {"unknown_key": ["active", "inactive"]}
    try:
        perspective_model.validate_facet_filters(bad_key_filters, enums)
        print("FAIL: Unknown facet key was not rejected")
        exit(1)
    except ValueError:
        pass

    # Assert an unknown value is rejected
    bad_value_filters = {"status": ["active", "unknown_value"]}
    try:
        perspective_model.validate_facet_filters(bad_value_filters, enums)
        print("FAIL: Unknown facet value was not rejected")
        exit(1)
    except ValueError:
        pass

    print("PASS")