from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_label_collisions

router = APIRouter(prefix="/api", tags=["api-keys"])


class LabelCollision(BaseModel):
    org_id: str
    label: str
    count: int
    key_ids: list[str]


class LabelCollisionResponse(BaseModel):
    ok: bool
    total_keys: int
    orgs_with_collisions: int
    collisions: list[LabelCollision]


@router.get(
    "/keys/label_collisions",
    response_model=LabelCollisionResponse,
    summary="Detect API keys sharing identical labels within an organization",
)
def list_label_collisions(
    session: Session = Depends(get_session),
) -> LabelCollisionResponse:
    payload = get_label_collisions(session)
    return LabelCollisionResponse(
        ok=True,
        total_keys=payload["total_keys"],
        orgs_with_collisions=payload["orgs_with_collisions"],
        collisions=payload["collisions"],
    )


if __name__ == "__main__":
    response = LabelCollisionResponse(
        ok=True,
        total_keys=5,
        orgs_with_collisions=1,
        collisions=[
            LabelCollision(
                org_id="org-1",
                label="production",
                count=2,
                key_ids=["key-1", "key-2"],
            )
        ],
    )
    assert response.ok is True
    assert response.total_keys == 5
    assert response.orgs_with_collisions == 1
    assert len(response.collisions) == 1
    assert response.collisions[0].count == 2
    assert "key_hash" not in response.model_dump()
    print("PASS")