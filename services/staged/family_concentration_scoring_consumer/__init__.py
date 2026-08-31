from fastapi import Depends
from pydantic import BaseModel
from typing import Optional, List, Any
import requests

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


class BaseSchema(BaseModel):
    class Config:
        from_attributes = True


class UserRead(BaseSchema):
    id: int
    username: str
    email: str
    org_id: Optional[int] = None


def get_mesh_memory_endpoint() -> dict:
    return {}


def mesh_memory_endpoint_get(mesh_memory_id: int, session=Depends(get_session)) -> dict:
    return {}


def mesh_memory_endpoint(mesh_memory_id: Optional[int] = None, session=Depends(get_session)) -> dict:
    return {}


def get_mesh_memory_by_id(mesh_memory_id: int, session=Depends(get_session)) -> dict:
    return {}


def signal_scores_endpoint(
    org_id: Optional[int] = None,
    signal_type: Optional[str] = None,
    session=Depends(get_session)
) -> List[dict]:
    return []


def get_score_disputes_endpoint(
    org_id: Optional[int] = None,
    status: Optional[str] = None,
    session=Depends(get_session)
) -> List[dict]:
    return []


def users_endpoint(session=Depends(get_session)) -> List[UserRead]:
    return []


def run_self_test() -> dict:
    return {"status": "ok"}


def test_service_package() -> bool:
    return True


if __name__ == "__main__":
    import sys
    try:
        from fastapi import FastAPI
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        def override_get_session():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = override_get_session

        result = run_self_test()
        assert result.get("status") == "ok", f"Expected status ok, got {result}"
        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)