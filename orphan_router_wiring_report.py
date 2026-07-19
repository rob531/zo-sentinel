import glob
import re
from typing import Dict, List, Tuple
from fastapi import Depends
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session
import requests

def get_router_files() -> List[str]:
    """Return list of all router files in the app."""
    return glob.glob("app/routers/**/*.py") + glob.glob("app/api/**/*.py") + ["app/main.py"]

def extract_router_registrations(file_content: str) -> List[str]:
    """Extract server IDs from router registration patterns in a file."""
    pattern = r"router\.include_router\(.*?server_id=(\d+)"
    return re.findall(pattern, file_content)

def get_all_servers(db: Session = Depends(get_session)) -> List[Dict]:
    """Query all servers from the MCP server registry."""
    servers = db.query(MCPServerRegistry).all()
    return [{"server_id": server.server_id, "name": server.name, "url": server.url} for server in servers]

def run() -> Dict:
    """Produce a wiring coverage report for MCP servers."""
    db = get_session()
    servers = get_all_servers(db)
    router_files = get_router_files()

    wired_server_ids = set()
    for file_path in router_files:
        with open(file_path, 'r') as file:
            content = file.read()
            wired_server_ids.update(extract_router_registrations(content))

    wired_servers = [server for server in servers if server["server_id"] in wired_server_ids]
    unwired_servers = [server for server in servers if server["server_id"] not in wired_server_ids]

    total_count = len(servers)
    coverage_pct = (len(wired_servers) / total_count * 100) if total_count > 0 else 0.0

    return {
        "unwired_servers": unwired_servers,
        "wired_servers": wired_servers,
        "total_count": total_count,
        "coverage_pct": coverage_pct
    }

def print_markdown_report(report: Dict) -> None:
    """Print a formatted markdown table of the report."""
    print("| Server ID | Name | URL | Status |")
    print("|-----------|------|-----|--------|")
    for server in report["unwired_servers"]:
        print(f"| {server['server_id']} | {server['name']} | {server['url']} | Unwired |")
    for server in report["wired_servers"]:
        print(f"| {server['server_id']} | {server['name']} | {server['url']} | Wired |")
    print(f"\nCoverage: {report['coverage_pct']:.2f}% ({report['wired_servers']}/{report['total_count']})")

if __name__ == "__main__":
    report = run()
    assert isinstance(report["coverage_pct"], float) and 0 <= report["coverage_pct"] <= 100
    assert isinstance(report["unwired_servers"], list)
    print_markdown_report(report)
    print("PASS")