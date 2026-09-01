# services/staged/server_family_dedup_api/logic.py
from collections import defaultdict
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Base  # Base is needed for test DB creation


router = APIRouter()


# ---------- Pydantic response models ----------
class ServerInfo(BaseModel):
    server_id: str = Field(..., description="Unique identifier of the server")
    name: str | None = Field(None, description="Human readable name")
    url: str = Field(..., description="Original URL of the server")
    risk_tier: str | None = Field(None, description="Risk tier classification")


class Family(BaseModel):
    family_key: str = Field(..., description="Derived family identifier")
    servers: list[ServerInfo] = Field(..., description="Servers belonging to the family")


class FamilyResponse(BaseModel):
    families: list[Family] = Field(..., description="List of families with duplicate/variant servers")


# ---------- Helper to derive family key ----------
def _derive_family_key(url: str) -> str:
    """
    Derive a family key from a URL by stripping version‑like or numeric
    path components. The algorithm is deliberately tolerant – it keeps
    the network location and the first two non‑numeric path parts.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path_parts = [p for p in parsed.path.split("/") if p]

    # Keep parts that are not pure numbers and not version/commit prefixes
    filtered = []
    for part in path_parts:
        lowered = part.lower()
        if lowered.isnumeric():
            continue
        if lowered.startswith("v") and lowered[1:].isdigit():
            continue
        if lowered.startswith("commit"):
            continue
        filtered.append(part)
        if len(filtered) == 2:  # we only need a short stable prefix
            break

    family_path = "/".join(filtered)
    return f"{netloc}/{family_path}" if family_path else netloc


# ---------- Endpoint ----------
@router.get(
    "/api/servers/family-dedup",
    response_model=FamilyResponse,
    summary="Group servers by derived family key",
)
def get_server_families(session: Session = Depends(get_session)) -> FamilyResponse:
    """
    Read all rows from ``McpServerRegistry`` and group them by a derived
    family key. Only families containing more than one server are returned.
    """
    records = session.query(McpServerRegistry).all()

    families: dict[str, list[ServerInfo]] = defaultdict(list)
    for rec in records:
        family_key = _derive_family_key(rec.url)
        server_info = ServerInfo(
            server_id=rec.server_id,
            name=rec.name,
            url=rec.url,
            risk_tier=rec.risk_tier,
        )
        families[family_key].append(server_info)

    # Keep only families with duplicates / variants
    result_families = [
        Family(family_key=key, servers=servers)
        for key, servers in families.items()
        if len(servers) > 1
    ]

    return FamilyResponse(families=result_families)


# ---------- Self‑test ----------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build an in‑memory SQLite engine and session factory
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)

    # Create tables based on the real model metadata
    Base.metadata.create_all(bind=engine)

    # Insert test data
    test_session = SessionLocal()
    test_data = [
        # Family 1 – GitHub repo
        McpServerRegistry(
            server_id="srv-1",
            name="GitHub Repo",
            url="https://github.com/org/repo",
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="GitHub Repo v2",
            url="https://github.com/org/repo/v2",
            risk_tier="low",
        ),
        # Family 2 – GitLab repo
        McpServerRegistry(
            server_id="srv-3",
            name="GitLab Repo",
            url="https://gitlab.com/other/repo",
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="GitLab Repo commit",
            url="https://gitlab.com/other/repo/commit/abc123",
            risk_tier="medium",
        ),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Override the dependency used by the endpoint
    def get_test_session() -> Session:
        return test_session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Directly call the endpoint logic (bypassing HTTP)
    response: FamilyResponse = get_server_families(test_session)

    # Assertions per acceptance criteria
    try:
        assert isinstance(response, FamilyResponse)
        assert len(response.families) == 2, f"expected 2 families, got {len(response.families)}"
        # One of the families must have exactly 2 servers
        counts = sorted([len(f.servers) for f in response.families])
        assert counts == [2, 2], f"expected each family to have 2 servers, got {counts}"
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")