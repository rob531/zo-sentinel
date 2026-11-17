from __future__ import annotations

from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_duplicate_hashes


router = APIRouter(
    prefix="/api",
    tags=["ask-corpus"],
)


class DuplicateServerGroup(BaseModel):
    content_hash: str
    doc_count: int = Field(ge=2)
    server_ids: List[str] = Field(default_factory=list)
    server_names: List[str] = Field(default_factory=list)
    first_indexed_at: Optional[datetime] = None
    last_indexed_at: Optional[datetime] = None


class DuplicateHashesResponse(BaseModel):
    generated_at: datetime
    total_duplicate_groups: int = Field(ge=0)
    total_duplicate_docs: int = Field(ge=0)
    total_wasted_docs: int = Field(ge=0)
    groups: List[DuplicateServerGroup] = Field(default_factory=list)


def _as_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    raise TypeError("Duplicate-hash logic returned an unsupported result type")


def _normalise_response(result: Any) -> Dict[str, Any]:
    payload = _as_dict(result)
    groups = payload.get("groups") or []

    normalised_groups: List[Dict[str, Any]] = []
    for group in groups:
        if hasattr(group, "model_dump"):
            group = group.model_dump()
        elif hasattr(group, "dict"):
            group = group.dict()

        doc_count = int(group.get("doc_count") or 0)
        normalised_groups.append(
            {
                "content_hash": str(group.get("content_hash") or ""),
                "doc_count": doc_count,
                "server_ids": [str(value) for value in (group.get("server_ids") or [])],
                "server_names": [
                    str(value) for value in (group.get("server_names") or [])
                ],
                "first_indexed_at": group.get("first_indexed_at"),
                "last_indexed_at": group.get("last_indexed_at"),
            }
        )

    normalised_groups.sort(
        key=lambda group: (
            group["doc_count"],
            group["content_hash"],
        ),
        reverse=True,
    )
    normalised_groups = normalised_groups[:100]

    total_duplicate_groups = payload.get("total_duplicate_groups")
    if total_duplicate_groups is None:
        total_duplicate_groups = len(normalised_groups)

    total_duplicate_docs = payload.get("total_duplicate_docs")
    if total_duplicate_docs is None:
        total_duplicate_docs = sum(group["doc_count"] for group in normalised_groups)

    total_wasted_docs = payload.get("total_wasted_docs")
    if total_wasted_docs is None:
        total_wasted_docs = sum(
            group["doc_count"] - 1 for group in normalised_groups
        )

    generated_at = payload.get("generated_at") or datetime.now(timezone.utc)

    return {
        "generated_at": generated_at,
        "total_duplicate_groups": int(total_duplicate_groups),
        "total_duplicate_docs": int(total_duplicate_docs),
        "total_wasted_docs": int(total_wasted_docs),
        "groups": normalised_groups,
    }


@router.get(
    "/ask-corpus/duplicates",
    response_model=DuplicateHashesResponse,
    summary="List duplicate ask-corpus content hashes",
)
async def get_ask_corpus_duplicates(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    result = get_duplicate_hashes(session)
    if isawaitable(result):
        result = await result
    return _normalise_response(result)


if __name__ == "__main__":
    route_paths = {route.path for route in router.routes}
    assert "/api/ask-corpus/duplicates" in route_paths
    print("PASS")