# deps: requests
"""NVD CVE 2.0 feed ingestor -- vuln-intel spine alongside ghsa_feed_ingestor.py."""
import os
import json
import logging
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
import requests
from fastapi import FastAPI
from pydantic import BaseModel

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
KILLSWITCH = "directives/.vuln_ingest_on"

logger = logging.getLogger(__name__)
app = FastAPI()


class HealthResponse(BaseModel):
    status: str


def _killswitch_active() -> bool:
    return os.path.exists(KILLSWITCH)


def fetch_page(start_index: int = 0) -> Dict:
    """Fetch a page of CVEs from NVD API 2.0 with 6s rate-limit sleep."""
    time.sleep(6)
    params = {"startIndex": start_index, "resultsPerPage": 2000}
    headers = {"Accept": "application/json"}

    for attempt in range(3):
        try:
            resp = requests.get(NVD_API_URL, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"NVD fetch attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise
    return {}


def parse_cve(record: Dict) -> Dict:
    """Parse NVD CVE 2.0 record into normalized dict."""
    cve = record.get("cve", {})
    cve_id = cve.get("id", "")

    # English description
    description = ""
    for desc in cve.get("descriptions", []):
        if desc.get("lang") == "en":
            description = desc.get("value", "")
            break

    # CVSS v3 score and severity
    cvss_v3_score = None
    cvss_severity = None
    metrics = cve.get("metrics", {})
    if "cvssMetricV31" in metrics:
        cvss_data = metrics["cvssMetricV31"][0].get("cvssData", {})
        cvss_v3_score = cvss_data.get("baseScore")
        cvss_severity = cvss_data.get("baseSeverity")
    elif "cvssMetricV30" in metrics:
        cvss_data = metrics["cvssMetricV30"][0].get("cvssData", {})
        cvss_v3_score = cvss_data.get("baseScore")
        cvss_severity = cvss_data.get("baseSeverity")
    elif "cvssMetricV2" in metrics:
        cvss_data = metrics["cvssMetricV2"][0].get("cvssData", {})
        cvss_v3_score = cvss_data.get("baseScore")
        cvss_severity = metrics["cvssMetricV2"][0].get("baseSeverity", "")

    published = cve.get("published", "")
    last_modified = cve.get("lastModified", "")

    # Reference URLs
    references = [ref.get("url", "") for ref in cve.get("references", []) if ref.get("url")]

    # Source info
    source_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
    fetched_at = datetime.utcnow().isoformat()

    # Content hash for idempotency
    content_hash = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()

    return {
        "cve_id": cve_id,
        "description": description,
        "cvss_v3_score": cvss_v3_score,
        "cvss_severity": cvss_severity,
        "published": published,
        "last_modified": last_modified,
        "references": references,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "content_hash": content_hash,
    }


def _write_to_write_service(rows: List[Dict]) -> bool:
    """Write rows to write_service with retry."""
    for row in rows:
        payload = {"table": "cve_advisories", "rows": row, "wait": True}
        for attempt in range(3):
            try:
                resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 2:
                    logger.error(f"write_service failed after 3 attempts: {e}")
                    return False
                time.sleep(2 ** attempt)
    return True


def _heartbeat() -> None:
    """Send heartbeat to service_health."""
    payload = {
        "table": "service_health",
        "rows": {
            "service": "nvd_cve_ingest_pipeline",
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
        },
        "wait": True,
    }
    try:
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except requests.RequestException as e:
        logger.error(f"Heartbeat failed: {e}")


def ingest(limit: int = 1000) -> None:
    """Main ingestion loop -- writes CVE advisories to write_service."""
    if not _killswitch_active():
        logger.info("Kill-switch not active; skipping NVD ingestion")
        return

    total_fetched = 0
    start_index = 0
    last_heartbeat = time.time()

    while total_fetched < limit:
        data = fetch_page(start_index)
        vulns = data.get("vulnerabilities", [])

        if not vulns:
            break

        rows = []
        for vuln in vulns:
            if total_fetched >= limit:
                break
            parsed = parse_cve(vuln)
            if parsed["cve_id"]:
                rows.append(parsed)
                total_fetched += 1

        if rows:
            if not _write_to_write_service(rows):
                logger.error("Failed to write batch to write_service")

        start_index += len(vulns)

        # Heartbeat every 60s
        if time.time() - last_heartbeat > 60:
            _heartbeat()
            last_heartbeat = time.time()

    _heartbeat()
    logger.info(f"Ingested {total_fetched} CVEs from NVD")


def run() -> None:
    """CLI entry point."""
    ingest(limit=10000)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return {"status": "healthy"}


if __name__ == "__main__":
    # Self-test: parse_cve must be real parsing
    record = {
        "cve": {
            "id": "CVE-2024-0001",
            "descriptions": [{"lang": "en", "value": "Test vulnerability"}],
            "published": "2024-01-01T00:00:00.000",
            "lastModified": "2024-01-02T00:00:00.000",
            "references": [{"url": "https://x.test"}],
            "metrics": {},
        }
    }
    result = parse_cve(record)
    assert result["cve_id"] == "CVE-2024-0001", f"cve_id mismatch: {result['cve_id']}"
    assert result["description"] == "Test vulnerability", f"description mismatch: {result['description']}"
    assert result["references"] == ["https://x.test"], f"references mismatch: {result['references']}"

    # Test with CVSS v3.1
    record_cvss = {
        "cve": {
            "id": "CVE-2024-0002",
            "descriptions": [{"lang": "en", "value": "Critical bug"}],
            "published": "2024-01-01T00:00:00.000",
            "lastModified": "2024-01-02T00:00:00.000",
            "references": [],
            "metrics": {
                "cvssMetricV31": [
                    {
                        "cvssData": {
                            "baseScore": 9.8,
                            "baseSeverity": "CRITICAL",
                        }
                    }
                ]
            },
        }
    }
    result_cvss = parse_cve(record_cvss)
    assert result_cvss["cvss_v3_score"] == 9.8, f"cvss_v3_score mismatch: {result_cvss['cvss_v3_score']}"
    assert result_cvss["cvss_severity"] == "CRITICAL", f"cvss_severity mismatch: {result_cvss['cvss_severity']}"

    # Test with missing English description
    record_no_en = {
        "cve": {
            "id": "CVE-2024-0003",
            "descriptions": [{"lang": "es", "value": "Test"}],
            "published": "2024-01-01T00:00:00.000",
            "lastModified": "2024-01-02T00:00:00.000",
            "references": [],
            "metrics": {},
        }
    }
    result_no_en = parse_cve(record_no_en)
    assert result_no_en["cve_id"] == "CVE-2024-0003"
    assert result_no_en["description"] == ""

    print("PASS")
