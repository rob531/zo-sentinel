from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import create_vuln_link, get_vuln_link
from .schema import VulnLinkCreate, VulnLinkResponse

router = APIRouter(prefix="/vuln_links", tags=["vuln_link_management"])


@router.post(
    "",
    response_model=VulnLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_vuln_link(
    payload: VulnLinkCreate,
    db: Session = Depends(get_session),
):
    try:
        link = create_vuln_link(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return VulnLinkResponse.from_orm(link)


@router.get(
    "/{link_id}",
    response_model=VulnLinkResponse,
)
def get_vuln_link_route(
    link_id: int,
    db: Session = Depends(get_session),
):
    link = get_vuln_link(db, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Vuln link not found")
    return VulnLinkResponse.from_orm(link)