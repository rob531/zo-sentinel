from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["registry-family-duplicates"])

MAX_REGISTRY_ROWS = 10_000
MIN_LIMIT = 1
MAX_LIMIT = 200


class DuplicateFamilyResponse(BaseModel):
    family_key: str
    member_count: int
    server_ids: List[str]
    names: List[str]
    avg_trust_score: float
    tiers: List[str]


class FamilyDuplicatesResponse(BaseModel):
    generated_at: datetime
    duplicate_groups: List[DuplicateFamilyResponse]
    total_duplicate_servers: int
    group_count: int


def _slug(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace("&", " and ")
    characters = []
    for character in normalized:
        if character.isalnum() or character in "._-":
            characters.append(character)
        else:
            characters.append("-")
    return "-".join("".join(characters).split("-")).strip("-._")


def _path_owner(value: str) -> Optional[str]:
    text = value.strip().lower()
    if not text:
        return None

    if "://" in text:
        parsed = urlsplit(text)
        segments = [unquote(part) for part in parsed.path.split("/") if part]
    elif "/" in text:
        without_scheme = text.removeprefix("https://").removeprefix("http://")
        without_scheme = without_scheme.removeprefix("www.")
        segments = [
            unquote(part)
            for part in without_scheme.split("/")
            if part and part not in {"github.com", "gitlab.com", "bitbucket.org"}
        ]
    else:
        segments = [text]

    if not segments:
        return None

    owner = segments[0].strip().lower()
    if owner.endswith(".git"):
        owner = owner[:-4]
    owner = _slug(owner)
    return owner or None


def family_key(name: Optional[str], url: Optional[str]) -> Optional[str]:
    url_owner = _path_owner(url or "")
    if url_owner:
        return url_owner

    name_owner = _path_owner(name or "")
    if name_owner:
        return name_owner

    return None


def _build_duplicate_groups(
    rows: Iterable[Sequence[Any]], limit: int
) -> List[DuplicateFamilyResponse]:
    grouped: Dict[str, List[Sequence[Any]]] = {}

    for row in rows:
        server_id = row[0]
        name = row[1]
        url = row[3]
        key = family_key(name, url)
        if not key or server_id is None:
            continue
        grouped.setdefault(key, []).append(row)

    duplicate_groups = []
    for key, members in grouped.items():
        if len(members) < 2:
            continue

        server_ids = sorted(str(member[0]) for member in members)
        names = sorted(str(member[1] or "") for member in members)
        tiers = sorted(str(member[6] or "unknown") for member in members)

        scores = []
        for member in members:
            score = member[4]
            if score is not None:
                scores.append(float(score))

        duplicate_groups.append(
            DuplicateFamilyResponse(
                family_key=key,
                member_count=len(members),
                server_ids=server_ids,
                names=names,
                avg_trust_score=(sum(scores) / len(scores)) if scores else 0.0,
                tiers=tiers,
            )
        )

    duplicate_groups.sort(key=lambda group: (-group.member_count, group.family_key))
    return duplicate_groups[:limit]


@router.get(
    "/registry/family-duplicates",
    response_model=FamilyDuplicatesResponse,
    summary="List duplicate registry families",
)
def get_registry_family_duplicates(
    session: Session = Depends(get_session),
    limit: int = Query(default=50),
) -> FamilyDuplicatesResponse:
    bounded_limit = max(MIN_LIMIT, min(limit, MAX_LIMIT))
    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.url,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.meta,
        )
        .order_by(McpServerRegistry.server_id)
        .limit(MAX_REGISTRY_ROWS)
    )
    rows = session.execute(statement).all()
    groups = _build_duplicate_groups(rows, bounded_limit)

    return FamilyDuplicatesResponse(
        generated_at=datetime.now(timezone.utc),
        duplicate_groups=groups,
        total_duplicate_servers=sum(group.member_count for group in groups),
        group_count=len(groups),
    )


if __name__ == "__main__":
    sample_rows = [
        ("server-1", "repo-a", "github", "https://github.com/acme/repo-a", 0.9, "trusted", "low", None, None, None),
        ("server-2", "repo-a", "gitlab", "http://www.github.com/acme/repo-a", 0.7, "trusted", "medium", None, None, None),
        ("server-3", "repo-b", "github", "https://github.com/other/repo-b", 0.8, "trusted", "low", None, None, None),
        ("server-4", "repo-b", "npm", "https://github.com/other/repo-b", 0.6, "trusted", "medium", None, None, None),
        ("server-5", "single", "github", "https://github.com/solo/project", 0.5, "unknown", "high", None, None, None),
        ("server-6", "another", "github", "https://github.com/lonely/project", 0.4, "unknown", "high", None, None, None),
    ]
    sample_groups = _build_duplicate_groups(sample_rows, 50)
    assert len(sample_groups) == 2
    assert all(group.member_count == 2 for group in sample_groups)
    assert sum(group.member_count for group in sample_groups) == 4
    print("PASS")