# deps: requests
import os
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional
import requests

logger = logging.getLogger(__name__)

NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
KILL_SWITCH_PATH = "directives/.vuln_ingest_on"
CURSOR_FILE = "/tmp/nvd_cve_cursor.txt"


def is_ingest_enabled() -> bool:
    return os.path.exists(KILL_SWITCH_PATH)


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


def fetch_page(start_index: int) -> dict:
    params = {
        "startIndex": start_index,
        "resultsPerPage": 100
    }
    try:
        response = requests.get(NVD_API_URL, params=params, timeout=30)
        response.raise_for_status()
        time.sleep(6)
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch NVD page at startIndex={start_index}: {e}")
        return {}


def parse_cve(record: dict) -> dict:
    cve = record.get("cve", {})
    cve_id = cve.get("id", "")

    description = ""
    for desc in cve.get("descriptions", []):
        if desc.get("lang") == "en":
            description = desc.get("value", "")
            break

    published = cve.get("published", "")
    last_modified = cve.get("lastModified", "")

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
        cvss_severity = metrics["cvssMetricV2"][0].get("baseSeverity")

    references = []
    for ref in cve.get("references", []):
        url = ref.get("url")
        if url:
            references.append(url)

    return {
        "cve_id": cve_id,
        "description": description,
        "cvss_v3_score": cvss_v3_score,
        "cvss_severity": cvss_severity,
        "published": published,
        "last_modified": last_modified,
        "references": references
    }


def write_to_write_service(cve_data: dict, fetched_at: str) -> bool:
    payload = {
        "table": "cve_advisories",
        "data": {
            "cve_id": cve_data["cve_id"],
            "description": cve_data["description"],
            "cvss_v3_score": cve_data["cvss_v3_score"],
            "cvss_severity": cve_data["cvss_severity"],
            "published": cve_data["published"],
            "last_modified": cve_data["last_modified"],
            "references": cve_data["references"],
            "source_url": f"https://nvd.nist.gov/vuln/detail/{cve_data['cve_id']}",
            "fetched_at": fetched_at
        }
    }

    for attempt in range(3):
        try:
            response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                logger.error(f"Failed to write CVE {cve_data['cve_id']} after 3 attempts: {e}")
                return False
            time.sleep(2 ** attempt)
    return False


def ingest(limit: int = 1000) -> int:
    if not is_ingest_enabled():
        logger.info("Ingest disabled (no .vuln_ingest_on file)")
        return 0

    start_index = 0
    total_ingested = 0
    fetched_at = datetime.utcnow().isoformat()

    saved_cursor = read_cursor()
    if saved_cursor:
        try:
            start_index = int(saved_cursor)
            logger.info(f"Resuming from startIndex={start_index}")
        except ValueError:
            pass

    while total_ingested < limit:
        data = fetch_page(start_index)
        vulnerabilities = data.get("vulnerabilities", [])

        if not vulnerabilities:
            logger.info(f"No more vulnerabilities at startIndex={start_index}")
            break

        for vuln in vulnerabilities:
            if total_ingested >= limit:
                break

            cve_data = parse_cve(vuln)
            if not cve_data["cve_id"]:
                continue

            if write_to_write_service(cve_data, fetched_at):
                total_ingested += 1

        start_index += 100
        write_cursor(str(start_index))

        logger.info(f"Ingested {total_ingested}/{limit} CVEs (startIndex={start_index})")

    logger.info(f"Completed ingest: {total_ingested} CVEs")
    return total_ingested


def run() -> int:
    return ingest(limit=1000)


if __name__ == "__main__":
    count = run()
    print(f"Ingested {count} CVEs")
