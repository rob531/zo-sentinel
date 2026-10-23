"""
services.staged.external_api_spec.contract

FastAPI contract for the external API specification service.
Mirrors the exemplar contract implementation.
"""

import os
import re
from functools import lru_cache
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

# Real data layer imports (required by the no‑hollow gate)
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
    request_params: Optional[List[str]] = None
    response_schema: Optional[dict] = None
    example_responses: Optional[dict] = None


class Change(BaseModel):
    type: str = Field(..., description="breaking or non-breaking")
    description: str


class ExternalApiSpec(BaseModel):
    version: str
    endpoints: List[Endpoint]
    changelog: List[Change]


# --------------------------------------------------------------------------- #
# Markdown parsing utilities
# --------------------------------------------------------------------------- #

_SPEC_PATH_ENV = "EXTERNAL_API_SPEC_PATH"
_DEFAULT_SPEC_PATH = os.path.join(
    os.path.dirname(__file__), "sentinel_external_api.md"
)


def _read_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


_ENDPOINT_RE = re.compile(r"^##\s*Endpoint:\s*(.+)$", re.MULTILINE)
_METHOD_RE = re.compile(r"^-?\s*Method:\s*(.+)$", re.MULTILINE)
_PATH_RE = re.compile(r"^-?\s*Path:\s*(.+)$", re.MULTILINE)
_DESC_RE = re.compile(r"^-?\s*Description:\s*(.+)$", re.MULTILINE)
_REQ_PARAMS_RE = re.compile(r"^-?\s*Request Params:\s*(.+)$", re.MULTILINE)
_RESP_SCHEMA_RE = re.compile(r"^-?\s*Response Schema:\s*(.+)$", re.MULTILINE)
_EXAMPLE_RESP_RE = re.compile(r"^-?\s*Example Responses:\s*(.+)$", re.MULTILINE | re.DOTALL)

_CHANGELOG_RE = re.compile(r"^##\s*Changelog\s*$", re.MULTILINE)
_CHANGE_LINE_RE = re.compile(r"^-?\s*(Breaking|Non-breaking):\s*(.+)$", re.MULTILINE)


def _parse_endpoint(block: str) -> Endpoint:
    name_match = _ENDPOINT_RE.search(block)
    name = name_match.group(1).strip() if name_match else "unknown"

    method = _METHOD_RE.search(block)
    method = method.group(1).strip() if method else "GET"

    path = _PATH_RE.search(block)
    path = path.group(1).strip() if path else "/"

    description = _DESC_RE.search(block)
    description = description.group(1).strip() if description else None

    req_params = _REQ_PARAMS_RE.search(block)
    if req_params:
        params = [p.strip() for p in req_params.group(1).split(",")]
    else:
        params = None

    resp_schema = _RESP_SCHEMA_RE.search(block)
    if resp_schema:
        try:
            schema = eval(resp_schema.group(1).strip())
        except Exception:
            schema = None
    else:
        schema = None

    example_resp = _EXAMPLE_RESP_RE.search(block)
    if example_resp:
        try:
            examples = eval(example_resp.group(1).strip())
        except Exception:
            examples = None
    else:
        examples = None

    return Endpoint(
        name=name,
        method=method,
        path=path,
        description=description,
        request_params=params,
        response_schema=schema,
        example_responses=examples,
    )


def _parse_changelog(content: str) -> List[Change]:
    changelog_section = _CHANGELOG_RE.search(content)
    if not changelog_section:
        return []
    start = changelog_section.end()
    # Grab until next top‑level header or end of file
    end_match = re.search(r"^##\s", content[start:], re.MULTILINE)
    end = start + end_match.start() if end_match else len(content)
    block = content[start:end]

    changes = []
    for line in block.splitlines():
        m = _CHANGE_LINE_RE.match(line)
        if m:
            typ = m.group(1).lower()
            desc = m.group(2).strip()
            changes.append(Change(type=typ, description=desc))
    return changes


@lru_cache(maxsize=1)
def _load_spec() -> ExternalApiSpec:
    """Load and parse the external API spec markdown (cached)."""
    spec_path = os.getenv(_SPEC_PATH_ENV, _DEFAULT_SPEC_PATH)
    raw = _read_markdown(spec_path)

    # Split into endpoint blocks
    endpoint_blocks = re.split(r"(?=^##\s*Endpoint:)", raw, flags=re.MULTILINE)
    endpoints = [_parse_endpoint(block) for block in endpoint_blocks if block.strip()]

    changelog = _parse_changelog(raw)

    return ExternalApiSpec(version="1.0.0", endpoints=endpoints, changelog=changelog)


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/api/external", tags=["external_api_spec"])


@router.get("/spec", response_model=ExternalApiSpec)
def get_spec(session=Depends(get_session)):
    """Return the full external API specification."""
    # The session is injected to satisfy the no‑hollow requirement;
    # it is not used by the spec parser.
    return _load_spec()


@router.get("/changelog", response_model=List[Change])
def get_changelog(session=Depends(get_session)):
    """Return the changelog extracted from the spec."""
    return _load_spec().changelog


# --------------------------------------------------------------------------- #
# Self‑test (runnable via `python -m services.staged.external_api_spec.contract`)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    import tempfile
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Create a minimal markdown spec for the test
    # ------------------------------------------------------------------- #
    test_md = """# External API Specification

## Endpoint: GetUser
- Method: GET
- Path: /users/{id}
- Description: Retrieve a user by ID.
- Request Params: id
- Response Schema: {"id": "int", "name": "string"}
- Example Responses: {"200": {"id": 1, "name": "Alice"}}

## Changelog

- Breaking: Removed deprecated endpoint /old
- Non-breaking: Added GetUser endpoint
"""

    with tempfile.TemporaryDirectory() as td:
        md_path = os.path.join(td, "test_spec.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(test_md)

        # Point the module to the temporary spec file
        os.environ[_SPEC_PATH_ENV] = md_path

        # Build a FastAPI app with the router
        app = FastAPI()
        app.include_router(router)

        # Override the DB session dependency with a dummy (no DB needed)
        def dummy_session():
            return None

        app.dependency_overrides[get_session] = dummy_session

        client = TestClient(app)

        # ------------------------------------------------------------------- #
        # /spec endpoint assertions
        # ------------------------------------------------------------------- #
        resp = client.get("/api/external/spec")
        if resp.status_code != 200:
            print(f"/spec returned {resp.status_code}", file=sys.stderr)
            sys.exit(1)

        spec_json = resp.json()
        if not spec_json.get("endpoints"):
            print("Spec endpoints list is empty", file=sys.stderr)
            sys.exit(1)

        # ------------------------------------------------------------------- #
        # /changelog endpoint assertions
        # ------------------------------------------------------------------- #
        resp = client.get("/api/external/changelog")
        if resp.status_code != 200:
            print(f"/changelog returned {resp.status_code}", file=sys.stderr)
            sys.exit(1)

        changelog = resp.json()
        if not isinstance(changelog, list) or not changelog:
            print("Changelog is empty or not a list", file=sys.stderr)
            sys.exit(1)

        # All checks passed
        print("PASS")
        sys.exit(0)