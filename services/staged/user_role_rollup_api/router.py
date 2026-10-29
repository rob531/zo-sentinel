from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_user_role_rollup as _get_user_role_rollup
except ImportError:
    from .logic import get_role_rollup as _get_user_role_rollup


class RoleCount(BaseModel):
    role: str
    count: int


class OrganisationRoleRollup(BaseModel):
    org_id: str
    org_name: str
    total: int
    by_role: List[RoleCount]


class UserRoleRollupResponse(BaseModel):
    total_users: int
    by_role: List[RoleCount]
    by_org: List[OrganisationRoleRollup]
    org_count: int


router = APIRouter(prefix="/api")


@router.get(
    "/orgs/users/role-rollup",
    response_model=UserRoleRollupResponse,
)
def get_role_rollup(
    session: Session = Depends(get_session),
) -> UserRoleRollupResponse:
    return _get_user_role_rollup(session)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    fixture = {
        "total_users": 4,
        "by_role": [
            {"role": "admin", "count": 1},
            {"role": "analyst", "count": 2},
            {"role": "viewer", "count": 1},
        ],
        "by_org": [
            {
                "org_id": "o1",
                "org_name": "Organisation One",
                "total": 2,
                "by_role": [
                    {"role": "admin", "count": 1},
                    {"role": "viewer", "count": 1},
                ],
            },
            {
                "org_id": "o2",
                "org_name": "Organisation Two",
                "total": 1,
                "by_role": [{"role": "analyst", "count": 1}],
            },
            {
                "org_id": "",
                "org_name": "unknown",
                "total": 1,
                "by_role": [{"role": "analyst", "count": 1}],
            },
        ],
        "org_count": 3,
    }

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: object()

    original_logic = _get_user_role_rollup
    try:
        globals()["_get_user_role_rollup"] = lambda session: fixture
        response = TestClient(test_app).get("/api/orgs/users/role-rollup")
        assert response.status_code == 200
        body = response.json()
        assert body["total_users"] == 4
        assert {"role": "admin", "count": 1} in body["by_role"]
        assert len(body["by_org"]) == 3
        assert {bucket["org_id"] for bucket in body["by_org"]} == {"o1", "o2", ""}
    finally:
        globals()["_get_user_role_rollup"] = original_logic

    print("PASS")


if __name__ == "__main__":
    _self_test()