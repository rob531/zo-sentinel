from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_github_pr_checker_status

router = APIRouter(prefix="/api", tags=["github_pr_checker"])


class CheckerStatus(BaseModel):
    total: int
    passed: int
    failed: int


class GithubPrCheckerResponse(BaseModel):
    checker_status: CheckerStatus


@router.get(
    "/integration/github/pr-checker",
    response_model=GithubPrCheckerResponse,
)
def github_pr_checker_status(
    session: Session = Depends(get_session),
) -> GithubPrCheckerResponse:
    return get_github_pr_checker_status(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpServerRegistry

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    def get_test_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    with TestSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="github-checker-1",
                    name="checker-1",
                    registry_source="github_pr_checker",
                    verdict="pass",
                ),
                McpServerRegistry(
                    server_id="github-checker-2",
                    name="checker-2",
                    registry_source="github_pr_checker",
                    verdict="pass",
                ),
                McpServerRegistry(
                    server_id="github-checker-3",
                    name="checker-3",
                    registry_source="github_pr_checker",
                    verdict="pass",
                ),
                McpServerRegistry(
                    server_id="github-checker-4",
                    name="checker-4",
                    registry_source="github_pr_checker",
                    verdict="fail",
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/integration/github/pr-checker")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "checker_status": {"total": 4, "passed": 3, "failed": 1}
    }, response.json()
    print("PASS")