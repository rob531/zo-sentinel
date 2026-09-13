# deps: fastapi, pydantic, sqlalchemy
"""Router for `registry_family_dedup` -- deduplicate server entries within families.

GET /api/registry-family-dedup/dedup-report
POST /api/registry-family-dedup/merge
"""
from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Optional

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["registry_family_dedup"])


def _derive_family_key(name: Optional[str], url: Optional[str]) -> str:
    """Derive a stable family key from server name or URL domain."""
    if url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            netloc = parsed.netloc.split(":")[0]
            netloc = netloc.lstrip("www.")
            if netloc:
                parts = netloc.split(".")
                if len(parts) >= 2:
                    return ".".join(parts[-2:])
                return parts[0]
        except Exception:
            pass
    if name:
        lower = name.lower()
        for sep in ("-", "_"):
            if sep in lower:
                prefix = lower.split(sep)[0]
                known = {
                    "github", "gitlab", "bitbucket", "openai", "anthropic",
                    "azure", "aws", "gcp", "slack", "discord", "notion",
                }
                if prefix in known:
                    return prefix
                return prefix
        return lower
    return "unknown"


class ServerEntry(BaseModel):
    server_id: str
    name: Optional[str]
    url: Optional[str]
    registry_source: Optional[str]
    risk_tier: Optional[str]
    trust_score: Optional[float]
    last_scored: Optional[datetime]


class FamilyGroup(BaseModel):
    family_key: str
    servers: list[ServerEntry]
    canonical_server_id: Optional[str]
    dedup_reason: str


class DedupReportResponse(BaseModel):
    families: list[FamilyGroup]
    total_families: int
    total_servers: int
    duplicates_found: int


class MergeRequest(BaseModel):
    canonical_server_id: str
    duplicate_server_ids: list[str]


class MergeResponse(BaseModel):
    merged: int
    canonical: str


@router.get("/registry-family-dedup/dedup-report", response_model=DedupReportResponse)
def get_dedup_report(
    db: Session = Depends(get_session),
) -> DedupReportResponse:
    """
    Produce a deduplication report: for each family, list servers that share
    the same family key (derived from name/URL domain) and flag potential
    duplicates based on high name similarity or shared URL netloc.
    """
    rows = db.query(McpServerRegistry).all()

    families: dict[str, list[McpServerRegistry]] = {}
    for srv in rows:
        fk = _derive_family_key(srv.name, srv.url)
        families.setdefault(fk, []).append(srv)

    groups: list[FamilyGroup] = []
    total_servers = 0
    duplicates_found = 0

    for fk, servers in families.items():
        total_servers += len(servers)
        canonical = max(
            servers,
            key=lambda s: (s.trust_score or 0, str(s.first_seen or "")),
        )

        entries = [
            ServerEntry(
                server_id=s.server_id,
                name=s.name,
                url=s.url,
                registry_source=s.registry_source,
                risk_tier=s.risk_tier,
                trust_score=s.trust_score,
                last_scored=s.last_scanned,
            )
            for s in servers
        ]

        dup_count = max(0, len(servers) - 1)
        duplicates_found += dup_count

        dedup_reason = (
            f"{len(servers)} servers share family '{fk}'; canonical={canonical.server_id}"
            if dup_count > 0
            else f"Single server in family '{fk}'"
        )

        groups.append(
            FamilyGroup(
                family_key=fk,
                servers=entries,
                canonical_server_id=canonical.server_id,
                dedup_reason=dedup_reason,
            )
        )

    groups.sort(key=lambda g: g.family_key)
    return DedupReportResponse(
        families=groups,
        total_families=len(groups),
        total_servers=total_servers,
        duplicates_found=duplicates_found,
    )


@router.post("/registry-family-dedup/merge", response_model=MergeResponse)
def merge_servers(
    req: MergeRequest,
    db: Session = Depends(get_session),
) -> MergeResponse:
    """
    Merge duplicate server IDs into a canonical server.
    Tags non-canonical servers with a meta field recording the merge target.
    """
    canonical = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == req.canonical_server_id
    ).first()
    if not canonical:
        raise ValueError(f"Canonical server not found: {req.canonical_server_id}")

    merged = 0
    for dup_id in req.duplicate_server_ids:
        dup = db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == dup_id
        ).first()
        if dup:
            meta = _json.loads(dup.meta or "{}")
            meta["registry_family_merge_target"] = canonical.server_id
            meta["registry_family_merged_at"] = datetime.utcnow().isoformat()
            dup.meta = _json.dumps(meta)
            merged += 1

    db.commit()
    return MergeResponse(merged=merged, canonical=canonical.server_id)


# ---------------------------------------------------------------------------
# Self‑test  (run: python services/active/registry_family_dedup/router.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = _override
    app.include_router(router)

    # Seed data
    with TestSession() as db:
        srv1 = McpServerRegistry(
            server_id="gh-1", name="github-mcp", url="https://api.github.com",
            registry_source="test", trust_score=0.95,
            risk_tier="low", meta="{}",
        )
        srv2 = McpServerRegistry(
            server_id="gh-2", name="github-tools", url="https://github.com",
            registry_source="test", trust_score=0.80,
            risk_tier="medium", meta="{}",
        )
        srv3 = McpServerRegistry(
            server_id="oi-1", name="openai-mcp", url="https://api.openai.com",
            registry_source="test", trust_score=0.90,
            risk_tier="high", meta="{}",
        )
        db.add_all([srv1, srv2, srv3])
        db.commit()

    client = TestClient(app)

    # Test dedup report
    resp = client.get("/api/registry-family-dedup/dedup-report")
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["total_families"] == 2, f"Expected 2 families, got {data}"
    assert data["total_servers"] == 3
    assert data["duplicates_found"] == 1  # gh-2 is dup of gh-1

    by_key = {f["family_key"]: f for f in data["families"]}
    github = by_key.get("github.com")
    assert github is not None, f"Missing github.com family: {data['families']}"
    assert len(github["servers"]) == 2
    assert github["canonical_server_id"] == "gh-1"

    # Test merge
    merge_resp = client.post(
        "/api/registry-family-dedup/merge",
        json={"canonical_server_id": "gh-1", "duplicate_server_ids": ["gh-2"]},
    )
    assert merge_resp.status_code == 200, f"Unexpected {merge_resp.status_code}: {merge_resp.text}"
    mdata = merge_resp.json()
    assert mdata["merged"] == 1
    assert mdata["canonical"] == "gh-1"

    # Verify tag applied
    with TestSession() as db:
        dup = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == "gh-2").first()
        assert dup is not None
        meta = _json.loads(dup.meta)
        assert meta.get("registry_family_merge_target") == "gh-1"

    print("PASS")
