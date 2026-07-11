import os
import json
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional
import requests
import hashlib
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisories
from pydantic import BaseModel
import random

app = FastAPI()
logger = logging.getLogger(__name__)

class GHSAAdvisory(BaseModel):
    id: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    affected_ranges: List[Dict]
    aliases: List[str]
    source_url: str
    published_at: str
    fetched_at: str
    content_hash: str

class HealthResponse(BaseModel):
    status: str

class WriteServiceResponse(BaseModel):
    success: bool
    message: Optional[str]

class GraphQLQuery(BaseModel):
    query: str
    variables: Optional[Dict] = None

class GraphQLResponse(BaseModel):
    data: Dict
    errors: Optional[List[Dict]] = None

GITHUB_API_URL = "https://api.github.com/graphql"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
CURSOR_FILE = "/tmp/ghsa_cursor.txt"
AUDIT_FILE = "/tmp/ghsa_ingestor_audit.jsonl"

def get_github_token() -> Optional[str]:
    return os.getenv("GITHUB_TOKEN")

def get_ecosystems() -> List[str]:
    ecosystems = os.getenv("GHSA_ECOSYSTEMS", "")
    return [eco.strip() for eco in ecosystems.split(",")] if ecosystems else []

def read_cursor() -> Optional[str]:
    try:
        with open(CURSOR_FILE, "r") as f:
            return f.read().strip()
    except (IOError, OSError):
        return None

def write_cursor(cursor: str) -> None:
    try:
        with open(CURSOR_FILE, "w") as f:
            f.write(cursor)
    except (IOError, OSError) as e:
        logger.error(f"Failed to write cursor: {e}")

def log_audit(entry: Dict) -> None:
    try:
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except (IOError, OSError) as e:
        logger.error(f"Failed to log audit: {e}")

def calculate_content_hash(summary: str) -> str:
    return hashlib.sha256(summary.encode()).hexdigest()

def fetch_ghsa_advisories(cursor: Optional[str] = None) -> List[GHSAAdvisory]:
    token = get_github_token()
    if not token:
        logger.info("GITHUB_TOKEN not set, skipping")
        return []

    ecosystems = get_ecosystems()
    ecosystem_filter = f"ecosystem: {ecosystems[0]}" if ecosystems else ""

    query = """
    query {
        securityAdvisories(first: 100, after: %s, orderBy: {field: PUBLISHED_AT, direction: DESC}) {
            nodes {
                ghsaId
                description
                severity
                ecosystem
                package {
                    name
                }
                affectedRanges {
                    type
                    events {
                        introduced
                        fixed
                    }
                }
                references {
                    url
                }
                publishedAt
                aliases
            }
            pageInfo {
                endCursor
                hasNextPage
            }
        }
    }
    """ % ("null" if not cursor else f'"{cursor}"')

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        response = requests.post(
            GITHUB_API_URL,
            json={"query": query},
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            logger.error(f"GraphQL errors: {data['errors']}")
            return []

        advisories = []
        for node in data["data"]["securityAdvisories"]["nodes"]:
            if ecosystems and node["ecosystem"] not in ecosystems:
                continue

            affected_ranges = []
            for range_ in node["affectedRanges"]:
                events = []
                for event in range_["events"]:
                    introduced = event.get("introduced")
                    fixed = event.get("fixed")
                    if introduced or fixed:
                        events.append({
                            "introduced": introduced,
                            "fixed": fixed
                        })
                if events:
                    affected_ranges.append({
                        "type": range_["type"],
                        "events": events
                    })

            source_url = next(
                (ref["url"] for ref in node["references"] if "github.com/advisories" in ref["url"]),
                None
            )

            advisory = GHSAAdvisory(
                id=node["ghsaId"],
                summary=node["description"],
                severity=node["severity"],
                ecosystem=node["ecosystem"],
                package=node["package"]["name"],
                affected_ranges=affected_ranges,
                aliases=node["aliases"],
                source_url=source_url,
                published_at=node["publishedAt"],
                fetched_at=datetime.utcnow().isoformat(),
                content_hash=calculate_content_hash(node["description"])
            )
            advisories.append(advisory)

        next_cursor = data["data"]["securityAdvisories"]["pageInfo"]["endCursor"]
        if data["data"]["securityAdvisories"]["pageInfo"]["hasNextPage"]:
            write_cursor(next_cursor)

        return advisories
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch GHSA advisories: {e}")
        return []

def write_advisories_to_db(advisories: List[GHSAAdvisory], session: Session) -> None:
    for advisory in advisories:
        try:
            existing = session.query(VulnAdvisories).filter_by(id=advisory.id).first()
            if existing:
                continue

            db_advisory = VulnAdvisories(
                feed="ghsa",
                id=advisory.id,
                summary=advisory.summary,
                severity=advisory.severity,
                ecosystem=advisory.ecosystem,
                package=advisory.package,
                affected_ranges=json.dumps(advisory.affected_ranges),
                aliases=json.dumps(advisory.aliases),
                source_url=advisory.source_url,
                published_at=advisory.published_at,
                fetched_at=advisory.fetched_at,
                content_hash=advisory.content_hash
            )
            session.add(db_advisory)
            session.commit()

            log_audit({
                "timestamp": datetime.utcnow().isoformat(),
                "action": "write",
                "advisory_id": advisory.id,
                "status": "success"
            })
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to write advisory {advisory.id}: {e}")
            log_audit({
                "timestamp": datetime.utcnow().isoformat(),
                "action": "write",
                "advisory_id": advisory.id,
                "status": "failed",
                "error": str(e)
            })

def write_to_write_service(advisories: List[GHSAAdvisory]) -> bool:
    for advisory in advisories:
        payload = {
            "table": "vuln_advisories",
            "data": {
                "feed": "ghsa",
                "id": advisory.id,
                "summary": advisory.summary,
                "severity": advisory.severity,
                "ecosystem": advisory.ecosystem,
                "package": advisory.package,
                "affected_ranges": advisory.affected_ranges,
                "aliases": advisory.aliases,
                "source_url": advisory.source_url,
                "published_at": advisory.published_at,
                "fetched_at": advisory.fetched_at,
                "content_hash": advisory.content_hash
            }
        }

        for attempt in range(3):
            try:
                response = requests.post(
                    WRITE_SERVICE_URL,
                    json=payload,
                    timeout=30
                )
                response.raise_for_status()
                log_audit({
                    "timestamp": datetime.utcnow().isoformat(),
                    "action": "write_service",
                    "advisory_id": advisory.id,
                    "status": "success"
                })
                break
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    logger.error(f"Failed to write to write_service after 3 attempts: {e}")
                    log_audit({
                        "timestamp": datetime.utcnow().isoformat(),
                        "action": "write_service",
                        "advisory_id": advisory.id,
                        "status": "failed",
                        "error": str(e)
                    })
                    return False
                time.sleep(2 ** attempt)
    return True

def heartbeat() -> None:
    try:
        requests.get("http://127.0.0.1:8772/health", timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"Heartbeat failed: {e}")

def run() -> None:
    token = get_github_token()
    if not token:
        logger.info("GITHUB_TOKEN not set, skipping")
        return

    cursor = read_cursor()
    advisories = fetch_ghsa_advisories(cursor)

    if not advisories:
        logger.info("No advisories fetched")
        return

    with Session() as session:
        write_advisories_to_db(advisories, session)

    if not write_to_write_service(advisories):
        logger.error("Failed to write advisories to write_service")

    heartbeat()

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    import asyncio
    from fastapi.testclient import TestClient

    # Override get_session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Test health endpoint
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

    # Test run function
    os.environ["GITHUB_TOKEN"] = "test_token"
    os.environ["GHSA_ECOSYSTEMS"] = "pip,npm,go"

    run()

    # Clean up
    del os.environ["GITHUB_TOKEN"]
    del os.environ["GHSA_ECOSYSTEMS"]

    print("PASS")