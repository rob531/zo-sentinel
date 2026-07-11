import time
import json
import hashlib
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from fastapi import FastAPI, Depends, HTTPException
from app.db import get_session
from app.models import MCPServerRegistry
from pydantic import BaseModel

app = FastAPI()

class Advisory(BaseModel):
    id: str
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    affected_ranges: List[str]
    aliases: List[str]
    source_url: str
    published_at: str
    fetched_at: str
    identities: List[str]
    content_hash: str

class Link(BaseModel):
    advisory_id: str
    server_id: str
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: str

def get_nvd_cves(last_mod_start: str, last_mod_end: str) -> Dict:
    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    params = {
        "lastModStartDate": last_mod_start,
        "lastModEndDate": last_mod_end,
        "per_page": 2000
    }
    headers = {"Accept": "application/json"}

    for attempt in range(3):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                raise
            wait_time = (2 ** attempt) + (0.5 * attempt)
            time.sleep(wait_time)

def parse_cve(cve: Dict) -> Optional[Advisory]:
    try:
        cve_data = cve["cve"]
        cve_id = cve_data["id"]
        summary = cve_data["description"]["description_data"][0]["value"]
        severity = cve_data["impact"]["baseMetricV3"]["cvssV3"]["baseSeverity"] if cve_data["impact"].get("baseMetricV3") else "UNKNOWN"
        ecosystem = "UNKNOWN"
        package = "UNKNOWN"
        affected_ranges = []
        aliases = [cve_id]

        for cpe in cve_data.get("affects", {}).get("cpe", {}).get("cpe_item", []):
            cpe_uri = cpe["cpe23Uri"]
            if "cpe:2.3:a:" in cpe_uri:
                parts = cpe_uri.split(":")
                if len(parts) >= 6:
                    ecosystem = parts[3]
                    package = parts[4]
                    affected_ranges.append(cpe_uri)

        source_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
        published_at = cve_data["publishedDate"]
        fetched_at = datetime.utcnow().isoformat()
        identities = [cve_id]

        content = json.dumps(cve, sort_keys=True).encode('utf-8')
        content_hash = hashlib.sha256(content).hexdigest()

        return Advisory(
            id=cve_id,
            feed="nvd",
            summary=summary,
            severity=severity,
            ecosystem=ecosystem,
            package=package,
            affected_ranges=affected_ranges,
            aliases=aliases,
            source_url=source_url,
            published_at=published_at,
            fetched_at=fetched_at,
            identities=identities,
            content_hash=content_hash
        )
    except (KeyError, IndexError):
        return None

def compute_score(metadata: Dict) -> Tuple[float, Dict]:
    score = 0.0
    evidence = {}

    if metadata.get("severity") == "CRITICAL":
        score += 90.0
        evidence["severity"] = "CRITICAL"
    elif metadata.get("severity") == "HIGH":
        score += 70.0
        evidence["severity"] = "HIGH"
    elif metadata.get("severity") == "MEDIUM":
        score += 50.0
        evidence["severity"] = "MEDIUM"
    elif metadata.get("severity") == "LOW":
        score += 30.0
        evidence["severity"] = "LOW"

    if metadata.get("ecosystem") and metadata.get("package"):
        evidence["package_match"] = f"{metadata['ecosystem']}/{metadata['package']}"
        score += 10.0

    if metadata.get("server_id"):
        evidence["server_match"] = metadata["server_id"]
        score += 5.0

    score = min(score, 100.0)
    return (score, evidence)

def write_to_service(data: Dict) -> None:
    url = "http://127.0.0.1:8772/write"
    headers = {"Content-Type": "application/json"}

    for attempt in range(3):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            return
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                raise
            wait_time = (2 ** attempt) + (0.5 * attempt)
            time.sleep(wait_time)

def heartbeat() -> None:
    data = {
        "table": "service_health",
        "data": {
            "service": "nvd_cve_feed_ingestor",
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat()
        }
    }
    write_to_service(data)

def run_ingestion_loop(session: MCPServerRegistry) -> None:
    last_heartbeat = time.time()

    while True:
        now = datetime.utcnow()
        last_mod_start = (now - timedelta(days=1)).isoformat()
        last_mod_end = now.isoformat()

        try:
            cves_data = get_nvd_cves(last_mod_start, last_mod_end)
            cves = cves_data.get("result", {}).get("CVE_Items", [])

            for cve in cves:
                advisory = parse_cve(cve)
                if advisory:
                    write_to_service({
                        "table": "vuln_advisories",
                        "data": advisory.dict()
                    })

                    servers = session.query(MCPServerRegistry).filter(
                        MCPServerRegistry.ecosystem == advisory.ecosystem,
                        MCPServerRegistry.package == advisory.package
                    ).all()

                    for server in servers:
                        score, evidence = compute_score({
                            "severity": advisory.severity,
                            "ecosystem": advisory.ecosystem,
                            "package": advisory.package,
                            "server_id": server.id
                        })

                        link = Link(
                            advisory_id=advisory.id,
                            server_id=server.id,
                            match_basis="cpe_match",
                            match_value=f"{advisory.ecosystem}/{advisory.package}",
                            match_confidence=0.9,
                            linked_at=datetime.utcnow().isoformat()
                        )
                        write_to_service({
                            "table": "vuln_links",
                            "data": link.dict()
                        })

            if time.time() - last_heartbeat > 60:
                heartbeat()
                last_heartbeat = time.time()

        except Exception as e:
            print(f"Error during ingestion: {e}")

        time.sleep(6 * 60 * 60)

def run() -> None:
    app.dependency_overrides[get_session] = lambda: get_session()
    run_ingestion_loop(get_session())

if __name__ == "__main__":
    # Mock test
    class MockResponse:
        def __init__(self, json_data, status_code):
            self.json_data = json_data
            self.status_code = status_code

        def json(self):
            return self.json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise HTTPException(status_code=self.status_code)

    def mock_get(*args, **kwargs):
        if "services.nvd.nist.gov" in args[0]:
            return MockResponse({
                "result": {
                    "CVE_Items": [{
                        "cve": {
                            "id": "CVE-2023-1234",
                            "description": {
                                "description_data": [{
                                    "value": "Test vulnerability description"
                                }]
                            },
                            "impact": {
                                "baseMetricV3": {
                                    "cvssV3": {
                                        "baseSeverity": "HIGH"
                                    }
                                }
                            },
                            "affects": {
                                "cpe": {
                                    "cpe_item": [{
                                        "cpe23Uri": "cpe:2.3:a:example:package:1.0:*:*:*:*:*:*:*"
                                    }]
                                }
                            },
                            "publishedDate": "2023-01-01T00:00:00Z"
                        }
                    }]
                }
            }, 200)
        return MockResponse({}, 200)

    def mock_post(*args, **kwargs):
        return MockResponse({}, 200)

    requests.get = mock_get
    requests.post = mock_post

    class MockSession:
        def query(self, model):
            return self

        def filter(self, *args):
            return self

        def all(self):
            return [MCPServerRegistry(id="server1", ecosystem="example", package="package")]

    app.dependency_overrides[get_session] = lambda: MockSession()

    try:
        run_ingestion_loop(get_session())
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")