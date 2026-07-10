# osv_feed_ingestor.py
import time
import uuid
import json
import hashlib
import requests
import threading
from datetime import datetime, timezone
from typing import Optional, Any

from app.db import get_session
from app.models import MCPServerRegistry

try:
    from app.models import VulnAdvisory, VulnLink
    VULN_MODELS_EXIST = True
except ImportError:
    VULN_MODELS_EXIST = False

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
OSV_API_URL = "https://api.osv.dev/v1/query"
OSV_API_TIMEOUT = 10

_heartbeat_stop = threading.Event()
_heartbeat_thread: Optional[threading.Thread] = None


def _heartbeat() -> None:
    while not _heartbeat_stop.is_set():
        try:
            requests.post(
                f"{WRITE_SERVICE_URL}/health",
                json={"component": "osv_feed_ingestor", "status": "running"},
                timeout=2
            )
        except Exception:
            pass
        time.sleep(60)


def start_heartbeat() -> None:
    global _heartbeat_thread
    _heartbeat_stop.clear()
    _heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
    _heartbeat_thread.start()


def stop_heartbeat() -> None:
    _heartbeat_stop.set()


def _query_write_service(sql: str, params: Optional[dict] = None) -> list:
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql, "params": params or {}},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        print(f"Write service query failed: {e}")
        return []


def _write_write_service(records: list, table: str) -> int:
    if not records:
        return 0
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "records": records},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("written", len(records))
    except Exception as e:
        print(f"Write service write failed: {e}")
        return 0


def _fetch_existing_advisory_ids() -> set:
    rows = _query_write_service("SELECT id FROM vuln_advisories")
    return {row.get("id") for row in rows if row.get("id")}


def _fetch_existing_advisory_hashes() -> set:
    rows = _query_write_service("SELECT content_hash FROM vuln_advisories WHERE content_hash IS NOT NULL")
    return {row.get("content_hash") for row in rows if row.get("content_hash")}


def _compute_content_hash(data: dict) -> str:
    normalized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode()).hexdigest()


def _query_osv_api(package: str, ecosystem: str) -> list:
    try:
        resp = requests.post(
            OSV_API_URL,
            json={"package": {"name": package, "ecosystem": ecosystem}},
            timeout=OSV_API_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json().get("vulns", [])
    except Exception as e:
        print(f"OSV API query failed for {ecosystem}/{package}: {e}")
        return []


def _transform_osv_vuln(vuln: dict, feed: str) -> dict:
    vuln_id = vuln.get("id", str(uuid.uuid4()))
    published = vuln.get("published", "")
    modified = vuln.get("modified", "")
    
    summary = ""
    for desc in vuln.get("descriptions", []):
        if desc.get("type") == "markdown":
            summary = desc.get("value", "")[:1000]
            break
    if not summary:
        for desc in vuln.get("descriptions", []):
            summary = desc.get("value", "")[:1000]
            break
    
    severity = None
    for severity_info in vuln.get("severity", []):
        if severity_info.get("score"):
            severity = severity_info.get("score")
            break
    
    affected = vuln.get("affected", [{}])[0] if vuln.get("affected") else {}
    ecosystem = affected.get("ecosystem", feed)
    package = affected.get("package", {}).get("name", "")
    
    affected_ranges = json.dumps(affected.get("ranges", []))
    aliases = json.dumps(vuln.get("aliases", []))
    identities = json.dumps({})
    
    source_url = f"https://osv.dev/vulnerability/{vuln_id}"
    
    return {
        "id": vuln_id,
        "feed": feed,
        "summary": summary,
        "severity": severity,
        "ecosystem": ecosystem,
        "package": package,
        "affected_ranges": affected_ranges,
        "aliases": aliases,
        "source_url": source_url,
        "published_at": published,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "identities": identities,
        "content_hash": _compute_content_hash(vuln)
    }


def ingest_osv_feed(feed: str = "pypi") -> int:
    start_heartbeat()
    existing_ids = _fetch_existing_advisory_ids()
    existing_hashes = _fetch_existing_advisory_hashes()
    
    records_to_write = []
    seen_hashes = set()
    
    with next(get_session()) as session:
        servers = session.query(MCPServerRegistry).all()
        packages = []
        for server in servers:
            meta = getattr(server, "meta", None) or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            server_packages = meta.get("packages", [])
            for pkg in server_packages:
                packages.append({
                    "ecosystem": pkg.get("ecosystem", feed),
                    "package": pkg.get("package", ""),
                    "server_id": str(server.id) if hasattr(server, "id") else None
                })
    
    all_vulns = []
    for pkg_info in packages:
        pkg_name = pkg_info["package"]
        ecosystem = pkg_info["ecosystem"]
        vulns = _query_osv_api(pkg_name, ecosystem)
        all_vulns.extend(vulns)
    
    if not packages:
        ecosystem_map = {"pypi": "PyPI", "npm": "npm", "go": "Go", "cargo": "crates.io", "maven": "Maven"}
        api_ecosystem = ecosystem_map.get(feed, feed)
        all_vulns = _query_osv_api("", api_ecosystem)
    
    for vuln in all_vulns:
        transformed = _transform_osv_vuln(vuln, feed)
        content_hash = transformed.get("content_hash")
        
        if transformed["id"] in existing_ids:
            continue
        if content_hash and content_hash in existing_hashes:
            continue
        if content_hash and content_hash in seen_hashes:
            continue
        
        seen_hashes.add(content_hash)
        records_to_write.append(transformed)
    
    count = _write_write_service(records_to_write, "vuln_advisories")
    return count


def link_advisories_to_servers() -> int:
    with next(get_session()) as session:
        servers = session.query(MCPServerRegistry).all()
        server_packages = {}
        for server in servers:
            if not hasattr(server, "id"):
                continue
            server_id = str(server.id)
            meta = getattr(server, "meta", None) or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            pkgs = meta.get("packages", [])
            server_packages[server_id] = [(p.get("ecosystem", ""), p.get("package", "")) for p in pkgs]
    
    existing_links = _query_write_service(
        "SELECT advisory_id, server_id FROM vuln_links"
    )
    existing_pairs = {(l.get("advisory_id"), l.get("server_id")) for l in existing_links}
    
    advisories = _query_write_service(
        "SELECT id, ecosystem, package, feed FROM vuln_advisories WHERE package IS NOT NULL AND package != ''"
    )
    
    links_to_write = []
    for advisory in advisories:
        adv_id = advisory.get("id")
        adv_ecosystem = advisory.get("ecosystem", "")
        adv_package = advisory.get("package", "")
        
        if not adv_id or not adv_package:
            continue
        
        for server_id, pkgs in server_packages.items():
            for pkg_ecosystem, pkg_name in pkgs:
                if adv_package == pkg_name:
                    pair = (adv_id, server_id)
                    if pair in existing_pairs:
                        continue
                    
                    if adv_ecosystem and pkg_ecosystem and adv_ecosystem.lower() == pkg_ecosystem.lower():
                        confidence = 1.0
                        match_basis = "exact_package_and_ecosystem"
                    else:
                        confidence = 0.7
                        match_basis = "exact_package_name"
                    
                    if confidence > 0.5:
                        links_to_write.append({
                            "advisory_id": adv_id,
                            "server_id": server_id,
                            "match_basis": match_basis,
                            "match_value": adv_package,
                            "match_confidence": confidence,
                            "linked_at": datetime.now(timezone.utc).isoformat()
                        })
                    existing_pairs.add(pair)
    
    count = _write_write_service(links_to_write, "vuln_links")
    return count


def run() -> None:
    print("OSV Feed Ingestor starting...")
    try:
        count = ingest_osv_feed("pypi")
        print(f"Ingested {count} new advisories from PyPI feed")
        
        link_count = link_advisories_to_servers()
        print(f"Linked {link_count} advisories to servers")
        
        print("OSV Feed Ingestor completed successfully")
    except Exception as e:
        print(f"OSV Feed Ingestor failed: {e}")
        raise
    finally:
        stop_heartbeat()


if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    from app.db import get_session
    from app import dependency_overrides
    
    def test_session():
        return Session()
    
    dependency_overrides[get_session] = test_session
    
    with Session() as session:
        from app.models import MCPServerRegistry
        
        test_server = MCPServerRegistry(
            name="test-mcp-server",
            meta=json.dumps({
                "packages": [
                    {"ecosystem": "PyPI", "package": "django"},
                    {"ecosystem": "npm", "package": "lodash"}
                ]
            }),
            status="active"
        )
        session.add(test_server)
        session.commit()
        server_id = test_server.id
    
    from app.models import VulnAdvisory, VulnLink
    
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
    
    test_advisories = [
        {
            "id": "OSV-TEST-001",
            "feed": "pypi",
            "summary": "Test advisory for django",
            "severity": "HIGH",
            "ecosystem": "PyPI",
            "package": "django",
            "affected_ranges": json.dumps([{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "4.0.0"}]}]),
            "aliases": json.dumps(["CVE-2023-TEST"]),
            "source_url": "https://osv.dev/vulnerability/OSV-TEST-001",
            "published_at": "2023-01-01T00:00:00Z",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "identities": json.dumps({}),
            "content_hash": hashlib.sha256(b"test_django_vuln").hexdigest()
        },
        {
            "id": "OSV-TEST-002",
            "feed": "npm",
            "summary": "Test advisory for lodash",
            "severity": "MEDIUM",
            "ecosystem": "npm",
            "package": "lodash",
            "affected_ranges": json.dumps([{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "4.17.21"}]}]),
            "aliases": json.dumps(["CVE-2023-TEST-2"]),
            "source_url": "https://osv.dev/vulnerability/OSV-TEST-002",
            "published_at": "2023-01-15T00:00:00Z",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "identities": json.dumps({}),
            "content_hash": hashlib.sha256(b"test_lodash_vuln").hexdigest()
        }
    ]
    
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "vuln_advisories", "records": test_advisories},
            timeout=30
        )
        if resp.status_code not in (200, 201):
            print(f"Seed failed: {resp.text}")
    except Exception as e:
        print(f"Seed write skipped (service may not be running): {e}")
    
    try:
        link_count = link_advisories_to_servers()
        links = _query_write_service("SELECT * FROM vuln_links")
        
        high_confidence_links = [l for l in links if l.get("match_confidence", 0) > 0.5]
        
        if high_confidence_links and link_count > 0:
            print("PASS")
        else:
            print(f"FAIL: Expected high-confidence links, got {high_confidence_links}")
            sys.exit(1)
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)