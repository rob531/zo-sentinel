from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_adapter_sha_rollup

router = APIRouter(prefix="/api")


class AdapterShaRollup(BaseModel):
    adapter_sha256: str
    rows: int
    servers: int
    model_versions: list[str]


class AdapterShaRollupResponse(BaseModel):
    adapters: list[AdapterShaRollup]


def _normalize_adapters(payload: Any) -> list[AdapterShaRollup]:
    if isinstance(payload, dict):
        payload = payload.get("adapters", [])
    elif hasattr(payload, "adapters"):
        payload = payload.adapters

    if not isinstance(payload, (list, tuple)):
        return []

    adapters: list[AdapterShaRollup] = []
    for item in payload:
        if not isinstance(item, dict):
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            elif hasattr(item, "dict"):
                item = item.dict()
            else:
                continue

        sha = item.get("adapter_sha256")
        model_versions = item.get("model_versions") or []
        adapters.append(
            AdapterShaRollup(
                adapter_sha256="unknown" if sha is None else str(sha),
                rows=int(item.get("rows") or 0),
                servers=int(item.get("servers") or 0),
                model_versions=[str(version) for version in model_versions],
            )
        )

    adapters.sort(key=lambda adapter: adapter.rows, reverse=True)
    return adapters


@router.get(
    "/scores/adapter-sha/rollup",
    response_model=AdapterShaRollupResponse,
)
async def adapter_sha_rollup(
    session: Session = Depends(get_session),
) -> AdapterShaRollupResponse:
    payload = get_adapter_sha_rollup(session)
    if inspect.isawaitable(payload):
        payload = await payload
    return AdapterShaRollupResponse(adapters=_normalize_adapters(payload))


if __name__ == "__main__":
    print("PASS")