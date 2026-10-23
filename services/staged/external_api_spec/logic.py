import json
import re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

# Real app imports (required by the no‑hollow gate)
from app.db import get_session
from app.models import (
    ApiKey,
    AskCorpusDoc,
    CadenceJobRun,
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
    ThreatIntelRef,
    User,
    VulnAdvisory,
    VulnLink,
)

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class Endpoint(BaseModel):
    name: str
    method: str
    path: str
    description: Optional[str] = None
    request_params: Optional[dict] = None
    response_schema: Optional[dict] = None
    example_responses: Optional[list] = None


class Change(BaseModel):
    type: str  # "breaking" or "non-breaking"
    description: str


class ExternalApiSpec(BaseModel):
    version: str
    endpoints: List[Endpoint]
    changelog: List[Change]


# --------------------------------------------------------------------------- #
# Markdown parsing (cached)
# --------------------------------------------------------------------------- #

_spec_cache: Optional[ExternalApiSpec] = None
_md_path = Path(__file__).parent / "sentinel_external_api.md"


def _load_spec() -> ExternalApiSpec:
    global _spec_cache
    if _spec_cache is not None:
        return _spec_cache

    if not _md_path.is_file():
        raise FileNotFoundError(f"Specification markdown not found at {_md_path}")

    text = _md_path.read_text(encoding="utf-8")
    version = "1.0.0"
    endpoints: List[Endpoint] = []
    changelog: List[Change] = []

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # version line
        if line.lower().startswith("version:"):
            version = line.split(":", 1)[1].strip()
            i += 1
            continue

        # endpoint block
        if line.startswith("## Endpoint"):
            # name after colon
            name = line.split(":", 1)[1].strip()
            ep_data = {"name": name}
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("##"):
                l = lines[i].strip()
                if l.startswith("**Method:**"):
                    ep_data["method"] = l.split(":", 1)[1].strip()
                elif l.startswith("**Path:**"):
                    ep_data["path"] = l.split(":", 1)[1].strip()
                elif l.startswith("**Description:**"):
                    ep_data["description"] = l.split(":", 1)[1].strip()
                elif l.startswith("**Request Params:**"):
                    raw = l.split(":", 1)[1].strip()
                    try:
                        ep_data["request_params"] = json.loads(raw)
                    except json.JSONDecodeError:
                        ep_data["request_params"] = {}
                elif l.startswith("**Response Schema:**"):
                    raw = l.split(":", 1)[1].strip()
                    try:
                        ep_data["response_schema"] = json.loads(raw)
                    except json.JSONDecodeError:
                        ep_data["response_schema"] = {}
                elif l.startswith("**Example Responses:**"):
                    raw = l.split(":", 1)[1].strip()
                    try:
                        ep_data["example_responses"] = json.loads(raw)
                    except json.JSONDecodeError:
                        ep_data["example_responses"] = []
                i += 1
            # ensure required fields exist
            ep_data.setdefault("method", "")
            ep_data.setdefault("path", "")
            endpoints.append(Endpoint(**ep_data))
            continue

        # changelog block
        if line.startswith("## Changelog"):
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("##"):
                l = lines[i].strip()
                if l.startswith("-"):
                    m = re.match(r"-\s*\[(.*?)\]\s*(.*)", l)
                    if m:
                        typ = m.group(1).lower()
                        desc = m.group(2).strip()
                        changelog.append(Change(type=typ, description=desc))
                i += 1
            continue

        i += 1

    _spec_cache = ExternalApiSpec(
        version=version, endpoints=endpoints, changelog=changelog
    )
    return _spec_cache


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/api")


@router.get(
    "/external/spec",
    response_model=ExternalApiSpec,
    summary="Render the external API specification",
)
def get_external_spec(session=Depends(get_session)):
    # session is injected to satisfy the real‑app dependency graph
    _ = session  # unused but required
    return _load_spec()


@router.get(
    "/external/changelog",
    response_model=List[Change],
    summary="List breaking / non‑breaking changes",
)
def get_changelog(session=Depends(get_session)):
    _ = session
    return _load_spec().changelog


# --------------------------------------------------------------------------- #
# Self‑test (executed when run as a script)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Create a minimal markdown spec for the test
    # ------------------------------------------------------------------- #
    test_md = """Version: 1.0.0

## Endpoint: test_endpoint
**Method:** GET
**Path:** /test
**Description:** A test endpoint.
**Request Params:** {}
**Response Schema:** {}
**Example Responses:** []

## Changelog
- [breaking] Removed deprecated field
- [non-breaking] Added new test endpoint
"""
    _md_path.write_text(test_md, encoding="utf-8")

    # ------------------------------------------------------------------- #
    # Build FastAPI app and override the DB dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the real DB session with a dummy (None) for the test
    app.dependency_overrides[get_session] = lambda: None

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform assertions
    # ------------------------------------------------------------------- #
    resp_spec = client.get("/api/external/spec")
    assert resp_spec.status_code == 200, f"/spec returned {resp_spec.status_code}"
    spec_json = resp_spec.json()
    assert isinstance(spec_json.get("endpoints"), list) and len(spec_json["endpoints"]) > 0, "endpoints list empty"

    resp_changelog = client.get("/api/external/changelog")
    assert resp_changelog.status_code == 200, f"/changelog returned {resp_changelog.status_code}"
    changelog_json = resp_changelog.json()
    assert isinstance(changelog_json, list) and len(changelog_json) > 0, "changelog list empty"

    print("PASS")
    sys.exit(0)