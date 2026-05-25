#!/usr/bin/env python3
"""
github_pr_checker.py -- ZO-SENTINEL GitHub PR checker utility.
Fetches PR diffs from GitHub API, identifies new MCP package.json entries
or mcp_config additions, looks them up in mcp_server_registry, and generates
safety assessment comments for PRs.
Requires GITHUB_TOKEN environment variable.
"""
import os
import re
import logging
import requests
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"

RISK_TIER_THRESHOLDS = {
    "trusted": 0.7,
    "caution": 0.4,
}

VERDICT_EMOJI = {
    "TRUSTED": "✅",
    "CAUTION": "⚠️",
    "HIGH_RISK": "🚨",
    "INSUFFICIENT": "❓",
    "UNKNOWN": "❓",
    "UNASSESSED": "❓",
}

VERDICT_COLOR = {
    "TRUSTED": "green",
    "CAUTION": "yellow",
    "HIGH_RISK": "red",
    "INSUFFICIENT": "gray",
    "UNKNOWN": "gray",
    "UNASSESSED": "gray",
}


def ws_query(sql: str) -> list:
    """Query the write_service query endpoint."""
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=30)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []


def get_github_headers() -> Dict[str, str]:
    """Get GitHub API headers with authentication token."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable not set")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }


def parse_pr_url(pr_url: str) -> Dict[str, str]:
    """Parse GitHub PR URL into components for API calls."""
    patterns = [
        r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<pr_number>\d+)",
        r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/compare/(?P<base>[^.]+)\.\.\.(?P<head>[^/]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, pr_url)
        if match:
            return match.groupdict()
    raise ValueError(f"Invalid GitHub PR URL: {pr_url}")


def fetch_pr_diff(owner: str, repo: str, pr_number: int) -> Optional[str]:
    """Fetch the diff content for a GitHub PR."""
    headers = get_github_headers()
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        pr_data = r.json()
        diff_url = pr_data.get("diff_url")
        if diff_url:
            diff_response = requests.get(diff_url, headers={"Authorization": f"token {os.environ.get('GITHUB_TOKEN')}"}, timeout=30)
            diff_response.raise_for_status()
            return diff_response.text
    except Exception as e:
        log.error(f"Failed to fetch PR diff: {e}")
    return None


def fetch_pr_files(owner: str, repo: str, pr_number: int) -> List[Dict[str, Any]]:
    """Fetch the list of files changed in a PR."""
    headers = get_github_headers()
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Failed to fetch PR files: {e}")
        return []


def extract_mcp_packages_from_diff(diff: str) -> List[str]:
    """Extract MCP package names from a diff (package.json dependencies)."""
    packages = []
    pattern = r'["\']name["\']\s*:\s*["\']([^"\']+)["\']'
    matches = re.findall(pattern, diff)
    for match in matches:
        if match.startswith("@modelcontextprotocol/") or "mcp" in match.lower():
            packages.append(match)
    return list(set(packages))


def extract_mcp_from_package_json(diff: str) -> List[str]:
    """Extract MCP packages from package.json diff content."""
    packages = []
    dep_patterns = [
        r'["\'](@modelcontextprotocol/[a-z_-]+)["\']\s*:\s*["\'][^"\']+["\']',
        r'["\'](@anthropic-ai/mcp-[a-z_-]+)["\']\s*:\s*["\'][^"\']+["\']',
        r'["\'](mcp-[a-z_-]+)["\']\s*:\s*["\'][^"\']+["\']',
    ]
    for pattern in dep_patterns:
        matches = re.findall(pattern, diff)
        packages.extend(matches)
    return list(set(packages))


def extract_mcp_from_config(diff: str) -> List[str]:
    """Extract MCP server entries from mcp_config or similar config files."""
    servers = []
    server_patterns = [
        r'mcpServers["\']?\s*[:=]\s*\{([^}]+)\}',
        r'"(https?://[^"]+\.json)"[^}]*',
        r'mcp_config\s*[:=]\s*\{([^}]+)\}',
    ]
    for pattern in server_patterns:
        matches = re.findall(pattern, diff, re.DOTALL)
        for match in matches:
            url_match = re.findall(r'https?://[^\s"\']+\.json', match)
            servers.extend(url_match)
            name_match = re.findall(r'["\']?name["\']?\s*[:=]\s*["\']([^"\']+)["\']', match)
            servers.extend(name_match)
    return list(set(servers))


def find_mcps_in_pr(diff: str, files: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Find all MCP entries mentioned in PR diff and files."""
    mcps = []
    seen = set()
    
    package_mcps = extract_mcp_from_package_json(diff)
    for name in package_mcps:
        if name not in seen:
            mcps.append({"name": name, "source": "package.json", "type": "npm_package"})
            seen.add(name)
    
    for file_info in files:
        filename = file_info.get("filename", "")
        if "package.json" in filename or "package-lock.json" in filename:
            patch = file_info.get("patch", "")
            pkg_mcps = extract_mcp_from_package_json(patch)
            for name in pkg_mcps:
                if name not in seen:
                    mcps.append({"name": name, "source": filename, "type": "npm_package"})
                    seen.add(name)
        elif any(x in filename for x in ["mcp_config", "mcp.json", ".mcp", "mcp_servers"]):
            patch = file_info.get("patch", "")
            config_mcps = extract_mcp_from_config(patch)
            for name in config_mcps:
                if name not in seen:
                    mcps.append({"name": name, "source": filename, "type": "config"})
                    seen.add(name)
    
    return mcps


def lookup_mcp_in_registry(name: str) -> Optional[Dict[str, Any]]:
    """Look up an MCP in the mcp_server_registry."""
    escaped_name = name.replace("'", "''")
    sql = f"""
        SELECT server_id, name, url, description, trust_score, verdict,
               verdict_reasoning, confidence, last_assessed, scan_count
        FROM mcp_server_registry
        WHERE name ILIKE '%{escaped_name}%'
           OR url ILIKE '%{escaped_name}%'
           OR server_id ILIKE '%{escaped_name}%'
        ORDER BY last_assessed DESC NULLS LAST
        LIMIT 1
    """
    rows = ws_query(sql)
    return rows[0] if rows else None


def determine_risk_tier(trust_score: Optional[float], verdict: str) -> str:
    """Determine risk tier based on trust score and verdict."""
    if verdict in ("HIGH_RISK", "DANGEROUS"):
        return "high_risk"
    if verdict == "TRUSTED":
        return "trusted"
    if trust_score is None:
        return "unknown"
    if trust_score >= RISK_TIER_THRESHOLDS["trusted"]:
        return "trusted"
    if trust_score >= RISK_TIER_THRESHOLDS["caution"]:
        return "caution"
    return "high_risk"


def format_verdict_badge(verdict: str) -> str:
    """Format verdict as a GitHub-style badge."""
    emoji = VERDICT_EMOJI.get(verdict.upper(), "❓")
    return f"{emoji} `{verdict}`"


def check_pr_for_mcps(pr_url: str) -> List[Dict[str, Any]]:
    """
    Check a GitHub PR for MCP entries and generate safety assessments.
    
    Args:
        pr_url: GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)
    
    Returns:
        List of assessment results for each MCP found in the PR.
    """
    try:
        parsed = parse_pr_url(pr_url)
    except ValueError as e:
        log.error(f"Invalid PR URL: {e}")
        return []

    owner = parsed["owner"]
    repo = parsed["repo"]
    pr_number = int(parsed["pr_number"])

    log.info(f"Checking PR {owner}/{repo}#{pr_number} for MCPs")

    diff = fetch_pr_diff(owner, repo, pr_number)
    files = fetch_pr_files(owner, repo, pr_number)
    
    if not diff and not files:
        log.warning(f"Could not fetch PR content for {pr_url}")
        return []

    mcp_entries = find_mcps_in_pr(diff or "", files)
    log.info(f"Found {len(mcp_entries)} MCP entries in PR")

    results = []
    for entry in mcp_entries:
        name = entry["name"]
        registry_entry = lookup_mcp_in_registry(name)
        
        if registry_entry:
            trust_score = registry_entry.get("trust_score")
            verdict = registry_entry.get("verdict") or "UNKNOWN"
            risk_tier = determine_risk_tier(trust_score, verdict)
            reason = registry_entry.get("verdict_reasoning") or "Found in registry"
            result = {
                "name": name,
                "source": entry["source"],
                "type": entry["type"],
                "verdict": verdict,
                "trust_score": trust_score,
                "risk_tier": risk_tier,
                "reason": reason,
                "confidence": registry_entry.get("confidence"),
                "last_assessed": registry_entry.get("last_assessed"),
                "server_id": registry_entry.get("server_id"),
                "url": registry_entry.get("url"),
            }
        else:
            result = {
                "name": name,
                "source": entry["source"],
                "type": entry["type"],
                "verdict": "UNASSESSED",
                "trust_score": None,
                "risk_tier": "unknown",
                "reason": "Not found in registry - requires assessment before merge",
                "confidence": None,
                "last_assessed": None,
                "server_id": None,
                "url": None,
            }
        
        results.append(result)

    return results


def generate_pr_comment(results: List[Dict[str, Any]], pr_url: str = "") -> str:
    """
    Generate a markdown PR comment summarizing MCP safety assessment.
    
    Args:
        results: List of assessment results from check_pr_for_mcps()
        pr_url: Optional PR URL for linking
    
    Returns:
        Markdown-formatted PR comment string.
    """
    if not results:
        return (
            "## 🔍 MCP Safety Assessment\n\n"
            "No MCP entries detected in this PR.\n"
        )

    header = "## 🔍 MCP Safety Assessment\n\n"
    header += f"This PR modifies **{len(results)}** MCP entr{'ies' if len(results) > 1 else 'y'}:\n\n"
    header += "| MCP | Verdict | Trust Score | Risk Tier | Source |\n"
    header += "|-----|----------|-------------|-----------|--------|\n"

    trusted = []
    caution = []
    high_risk = []
    unknown = []

    for r in results:
        name = r["name"]
        verdict = r["verdict"]
        score = r["trust_score"]
        tier = r["risk_tier"]
        source = r["source"]
        emoji = VERDICT_EMOJI.get(verdict.upper(), "❓")
        score_str = f"{score:.2f}" if score is not None else "—"
        header += f"| {emoji} `{name}` | {verdict} | {score_str} | {tier} | `{source}` |\n"
        
        if tier == "trusted":
            trusted.append(r)
        elif tier == "caution":
            caution.append(r)
        elif tier == "high_risk":
            high_risk.append(r)
        else:
            unknown.append(r)

    body = "\n---\n\n"

    if high_risk:
        body += "### 🚨 High Risk MCPS\n\n"
        for r in high_risk:
            body += f"**`{r['name']}`**\n"
            body += f"- Source: `{r['source']}`\n"
            body += f"- Verdict: {format_verdict_badge(r['verdict'])}\n"
            body += f"- Reason: {r['reason']}\n\n"
        body += "**⚠️ These MCPs have significant risk indicators. Review carefully before merge.**\n\n"

    if caution:
        body += "### ⚠️ Caution MCPS\n\n"
        for r in caution:
            body += f"**`{r['name']}`**\n"
            body += f"- Source: `{r['source']}`\n"
            body += f"- Verdict: {format_verdict_badge(r['verdict'])}\n"
            body += f"- Score: {r['trust_score']:.2f if r['trust_score'] else 0:.2f}\n"
            body += f"- Reason: {r['reason']}\n\n"

    if unknown:
        body += "### ❓ Unassessed MCPS\n\n"
        body += "These MCPs have not been assessed by ZO-SENTINEL:\n\n"
        for r in unknown:
            body += f"- **`{r['name']}`** (from `{r['source']}`)\n"
            body += f"  - Requires assessment before merge\n\n"

    if trusted:
        body += "### ✅ Trusted MCPS\n\n"
        for r in trusted:
            body += f"- **`{r['name']}`** — {format_verdict_badge(r['verdict'])}\n"
        body += "\n"

    footer = "\n---\n\n"
    footer += "*🤖 Generated by ZO-SENTINEL MCP Safety Intelligence*\n"
    
    return header + body + footer


def post_pr_comment(pr_url: str, comment: str) -> bool:
    """Post a comment to the GitHub PR."""
    try:
        parsed = parse_pr_url(pr_url)
        headers = get_github_headers()
        headers["Content-Type"] = "application/json"
        payload = {"body": comment}
        url = f"https://api.github.com/repos/{parsed['owner']}/{parsed['repo']}/issues/{parsed['pr_number']}/comments"
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        log.info(f"Posted comment to {pr_url}")
        return True
    except Exception as e:
        log.error(f"Failed to post PR comment: {e}")
        return False


def main():
    """CLI entry point for testing."""
    import argparse
    parser = argparse.ArgumentParser(description="Check GitHub PR for MCP entries")
    parser.add_argument("pr_url", help="GitHub PR URL")
    parser.add_argument("--post", action="store_true", help="Post comment to PR")
    args = parser.parse_args()

    if not os.environ.get("GITHUB_TOKEN"):
        print("Error: GITHUB_TOKEN environment variable not set")
        return 1

    results = check_pr_for_mcps(args.pr_url)
    comment = generate_pr_comment(results, args.pr_url)
    print(comment)

    if args.post:
        if post_pr_comment(args.pr_url, comment):
            print("\n✓ Comment posted successfully")
        else:
            print("\n✗ Failed to post comment")
            return 1

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())