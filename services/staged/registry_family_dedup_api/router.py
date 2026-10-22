from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry-family-duplicates"])


class DuplicateGroup(BaseModel):
    family_key: str
    member_count: int
    server_ids: List[Any]
    names: List[str]
    avg_trust_score: Optional[float]
    tiers: List[Optional[str]]


class FamilyDuplicateResponse(BaseModel):
    generated_at: str
    duplicate_groups: List[DuplicateGroup]
    total_duplicate_servers: int
    group_count: int


_PROTOCOL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
_SCP_LIKE_RE = re.compile(r"^[^/@]+@[^:]+:(.*)$")


def _identity_parts(value: Any) -> tuple[str, List[str]]:
    """Return a normalised identity and its path components."""
    if value is None:
        return "", []

    text = str(value).strip().lower()
    if not text:
        return "", []

    text = _PROTOCOL_RE.sub("", text)
    text = re.sub(r"^www\.", "", text)
    text = text.split("?", 1)[0].split("#", 1)[0]

    scp_match = _SCP_LIKE_RE.match(text)
    if scp_match:
        text = scp_match.group(1)

    text = text.strip("/")
    if not text:
        return "", []

    parts = [part for part in text.split("/") if part]
    if not parts:
        return "", []

    # A URL normally starts with its host.  The host itself must not be
    # used as the family key, otherwise every repository on a registry would
    # become one family.
    if len(parts) > 1:
        host = parts[0]
        path_parts = parts[1:]
        if host in {"github.com", "gitlab.com", "bitbucket.org"}:
            if path_parts and path_parts[0] in {"repos", "projects"}:
                path_parts = path_parts[1:]
            return host, path_parts
        return host, path_parts

    return "", parts


def _family_key(name: Any, url: Any) -> str:
    """Build a deterministic owner/repo family key.

    The URL is preferred when it contains a path because it identifies the
    upstream owner more reliably than a display name.  Names are used as a
    fallback and also help with registry entries whose URL is unavailable.
    """
    url_host, url_parts = _identity_parts(url)
    name_host, name_parts = _identity_parts(name)

    candidates: List[str] = []
    if url_parts:
        if url_parts[0] in {"repos", "projects"} and len(url_parts) > 1:
            candidates.append(url_parts[1])
        else:
            candidates.append(url_parts[0])

    if name_parts:
        if name_parts[0] in {"repos", "projects"} and len(name_parts) > 1:
            candidates.append(name_parts[1])
        else:
            candidates.append(name_parts[0])

    for candidate in candidates:
        candidate = candidate.strip().strip("/")
        if candidate:
            return candidate

    # A host is only a last-resort key.  It is retained so that malformed
    # entries are still grouped consistently rather than discarded silently.
    for host in (url_host, name_host):
        if host:
            return host

    return ""


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    value = getattr(row, name, default)
    return default if value is None else value


def _group_rows(rows: List[Any]) -> Dict[str, List[Any]]:
    grouped: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
        key = _family_key(
            _row_value(row, "name", ""),
            _row_value(row, "url", ""),
        )
        if key:
            grouped[key].append(row)
    return grouped


def _make_group(family_key: str, rows: List[Any]) -> DuplicateGroup:
    ordered_rows = sorted(
        rows,
        key=lambda row: str(_row_value(row, "server_id", "")),
    )

    server_ids = [
        _row_value(row, "server_id")
        for row in ordered_rows
        if _row_value(row, "server_id") is not None
    ]
    names = [
        str(_row_value(row, "name", ""))
        for row in ordered_rows
        if _row_value(row, "name") is not None
    ]
    tiers = [
        _row_value(row, "risk_tier")
        for row in ordered_rows
    ]

    trust_scores = []
    for row in ordered_rows:
        score = _row_value(row, "trust_score")
        try:
            if score is not None:
                trust_scores.append(float(score))
        except (TypeError, ValueError):
            continue

    average = round(sum(trust_scores) / len(trust_scores), 4) if trust_scores else None

    return DuplicateGroup(
        family_key=family_key,
        member_count=len(ordered_rows),
        server_ids=server_ids,
        names=names,
        avg_trust_score=average,
        tiers=tiers,
    )


@router.get(
    "/registry/family-duplicates",
    response_model=FamilyDuplicateResponse,
    summary="Find duplicate registry families",
)
def get_registry_family_duplicates(
    limit: int = Query(50, description="Maximum number of duplicate families to return"),
    session: Session = Depends(get_session),
) -> FamilyDuplicateResponse:
    limit = max(1, min(int(limit), 200))

    # Keep the database operation bounded and dialect-neutral.  Family keys
    # are deliberately calculated in Python after the rows have been read.
    rows = session.query(McpServerRegistry).limit(1000).all()
    grouped = _group_rows(rows)

    groups = [
        _make_group(family_key, family_rows)
        for family_key, family_rows in grouped.items()
        if len(family_rows) >= 2
    ]
    groups.sort(key=lambda group: (-group.member_count, group.family_key))

    total_duplicate_servers = sum(group.member_count for group in groups)
    returned_groups = groups[:limit]

    return FamilyDuplicateResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        duplicate_groups=returned_groups,
        total_duplicate_servers=total_duplicate_servers,
        group_count=len(returned_groups),
    )


if __name__ == "__main__":
    assert _family_key("Example Server", "https://github.com/acme/widgets") == "acme"
    assert _family_key("acme/widgets", "https://github.com/acme/widgets") == "acme"
    assert _family_key("Other", "https://github.com/other/widgets") == "other"
    print("PASS")