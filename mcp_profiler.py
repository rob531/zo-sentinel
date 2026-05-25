#!/usr/bin/env python3
"""
mcp_profiler.py -- ZO-SENTINEL MCP server profiling utility.
Profiles MCP servers by fetching manifest/package.json and extracting metadata.
Used as pre-assessment enrichment before signal scoring.
"""
import requests
import json
import logging
import hashlib
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse, urljoin
import re

try:
    from url_analyser import analyse_url
except ImportError:
    analyse_url = None

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
USER_AGENT = "ZO-SENTINEL-MCP-Profiler/1.0"

COMMON_MANIFEST_PATHS = [
    "/.well-known/mcp.json",
    "/mcp.json",
    "/manifest.json",
    "/package.json",
]

NPM_REGISTRY_URL = "https://registry.npmjs.org"
GITHUB_API_URL = "https://api.github.com"

PERMISSION_KEYWORDS = [
    "read", "write", "delete", "execute", "admin", "root",
    "filesystem", "network", "process", "memory", "env",
    "secrets", "credentials", "keys", "tokens", "passwords",
    "database", "sql", "http", "https", "websocket", "ssh",
    "exec", "spawn", "eval", "script", "plugin", "extension"
]

AUTHENTICATION_PATTERNS = [
    "api_key", "apiKey", "bearer", "token", "auth",
    "credentials", "oauth", "jwt", "session", "cookie",
    "x-api-key", "authorization", "basic"
]

ENCRYPTION_PATTERNS = [
    "tls", "ssl", "https", "encrypted", "encryption",
    "aes", "rsa", "crypto", "secure", "certificate", "pem", "key"
]


def compute_profile_hash(profile: Dict[str, Any]) -> str:
    normalized = json.dumps(profile, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def determine_server_type(url: str, manifest: Optional[Dict] = None) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    
    if "github.com" in domain:
        return "github"
    if "npmjs.com" in domain or "registry.npmjs.org" in domain:
        return "npm"
    if "vercel.com" in domain or "now.sh" in domain:
        return "vercel"
    if "fly.dev" in domain or "fly.io" in domain:
        return "fly"
    if "railway.app" in domain:
        return "railway"
    if "render.com" in domain:
        return "render"
    if "heroku.com" in domain:
        return "heroku"
    if "docker.io" in domain or "ghcr.io" in domain:
        return "docker"
    if path.endswith(".json") or (manifest and "version" in manifest and "name" in manifest):
        if "npm" in str(manifest) or "node" in str(manifest):
            return "npm"
        return "custom"
    
    return "custom"


def extract_tool_names(manifest: Dict) -> List[str]:
    tool_names = []
    
    if "tools" in manifest:
        tools = manifest["tools"]
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, str):
                    tool_names.append(tool)
                elif isinstance(tool, dict) and "name" in tool:
                    tool_names.append(tool["name"])
        elif isinstance(tools, dict):
            tool_names.extend(list(tools.keys()))
    
    if "endpoints" in manifest:
        endpoints = manifest["endpoints"]
        if isinstance(endpoints, list):
            for endpoint in endpoints:
                if isinstance(endpoint, dict) and "name" in endpoint:
                    tool_names.append(endpoint["name"])
                elif isinstance(endpoint, str):
                    tool_names.append(endpoint)
    
    if "commands" in manifest:
        commands = manifest["commands"]
        if isinstance(commands, list):
            for cmd in commands:
                if isinstance(cmd, str):
                    tool_names.append(cmd)
                elif isinstance(cmd, dict) and "name" in cmd:
                    tool_names.append(cmd["name"])
    
    if "functions" in manifest:
        functions = manifest["functions"]
        if isinstance(functions, list):
            for fn in functions:
                if isinstance(fn, str):
                    tool_names.append(fn)
                elif isinstance(fn, dict) and "name" in fn:
                    tool_names.append(fn["name"])
    
    if "actions" in manifest:
        actions = manifest["actions"]
        if isinstance(actions, list):
            for action in actions:
                if isinstance(action, str):
                    tool_names.append(action)
                elif isinstance(action, dict) and "name" in action:
                    tool_names.append(action["name"])
    
    return list(set(tool_names))


def extract_permission_list(manifest: Dict) -> List[str]:
    permissions = []
    manifest_str = json.dumps(manifest).lower()
    
    for perm in PERMISSION_KEYWORDS:
        if perm.lower() in manifest_str:
            permissions.append(perm)
    
    if "permissions" in manifest:
        perms = manifest["permissions"]
        if isinstance(perms, list):
            permissions.extend([p for p in perms if isinstance(p, str)])
        elif isinstance(perms, dict):
            permissions.extend([k for k, v in perms.items() if v])
        elif isinstance(perms, str):
            permissions.append(perms)
    
    if "scopes" in manifest:
        scopes = manifest["scopes"]
        if isinstance(scopes, list):
            permissions.extend([s for s in scopes if isinstance(s, str)])
        elif isinstance(scopes, str):
            permissions.append(scopes)
    
    if "capabilities" in manifest:
        caps = manifest["capabilities"]
        if isinstance(caps, list):
            permissions.extend([c for c in caps if isinstance(c, str)])
        elif isinstance(caps, dict):
            permissions.extend(list(caps.keys()))
    
    return list(set(permissions))


def check_has_authentication(manifest: Dict) -> bool:
    manifest_str = json.dumps(manifest)
    
    for pattern in AUTHENTICATION_PATTERNS:
        if pattern.lower() in manifest_str.lower():
            return True
    
    if "security" in manifest:
        return True
    
    if "auth" in manifest:
        return True
    
    return False


def check_has_encryption(manifest: Dict) -> bool:
    manifest_str = json.dumps(manifest)
    
    for pattern in ENCRYPTION_PATTERNS:
        if pattern.lower() in manifest_str.lower():
            return True
    
    if manifest.get("secure", False):
        return True
    
    if "security" in manifest:
        security = manifest["security"]
        if isinstance(security, dict) and security:
            return True
        if isinstance(security, list) and security:
            return True
    
    return False


def extract_declared_scope(manifest: Dict) -> Optional[str]:
    if "scope" in manifest:
        scope = manifest["scope"]
        if isinstance(scope, str):
            return scope
    
    if "namespace" in manifest:
        namespace = manifest["namespace"]
        if isinstance(namespace, str):
            return namespace
    
    if "domain" in manifest:
        domain = manifest["domain"]
        if isinstance(domain, str):
            return domain
    
    if "categories" in manifest:
        cats = manifest["categories"]
        if isinstance(cats, list) and cats:
            return cats[0]
        elif isinstance(cats, str):
            return cats
    
    return None


def fetch_from_npm(package_name: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict]:
    clean_name = package_name.replace("https://www.npmjs.com/package/", "").replace("http://", "").split("/")[-1]
    clean_name = clean_name.split("#")[0].split("?")[0]
    
    if not clean_name or clean_name.startswith("registry"):
        return None
    
    try:
        url = f"{NPM_REGISTRY_URL}/{clean_name}/latest"
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException as e:
        log.debug(f"NPM fetch failed for {package_name}: {e}")
    
    return None


def fetch_manifest_from_url(base_url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, */*"
    }
    
    for path in COMMON_MANIFEST_PATHS:
        try:
            manifest_url = urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
            response = requests.get(manifest_url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type or manifest_url.endswith(".json"):
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        pass
        except requests.RequestException:
            pass
    
    try:
        response = requests.get(base_url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    pass
    except requests.RequestException:
        pass
    
    return None


def fetch_github_repo_info(url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict]:
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")
    
    if len(path_parts) < 2 or parsed.netloc != "github.com":
        return None
    
    owner, repo = path_parts[0], path_parts[1]
    repo = repo.replace(".git", "")
    
    try:
        api_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github.v3+json"}
        response = requests.get(api_url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            return {
                "description": data.get("description"),
                "homepage": data.get("homepage"),
                "topics": data.get("topics", []),
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "language": data.get("language"),
                "license": data.get("license", {}).get("name") if data.get("license") else None
            }
    except requests.RequestException as e:
        log.debug(f"GitHub API fetch failed for {url}: {e}")
    
    return None


def fetch_package_json_from_github(url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict]:
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")
    
    if len(path_parts) < 2 or parsed.netloc != "github.com":
        return None
    
    owner, repo = path_parts[0], path_parts[1].replace(".git", "")
    
    try:
        api_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/package.json"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github.v3.raw"}
        response = requests.get(api_url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            try:
                return json.loads(response.text)
            except json.JSONDecodeError:
                pass
    except requests.RequestException:
        pass
    
    return None


def profile_mcp(url: str, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    profile = {
        "url": url,
        "fetched_at": None,
        "fetch_success": False,
        "fetch_error": None,
        "manifest": None,
        "tool_count": 0,
        "tool_names": [],
        "permission_list": [],
        "server_type": "unknown",
        "has_authentication": False,
        "has_encryption": False,
        "declared_scope": None,
        "url_analysis": None,
        "profile_hash": None,
        "package_info": None,
        "github_info": None,
        "metadata": {}
    }
    
    from datetime import datetime, timezone
    profile["fetched_at"] = datetime.now(timezone.utc).isoformat()
    
    try:
        url_result = analyse_url(url) if analyse_url else None
        if url_result:
            profile["url_analysis"] = {
                "domain": url_result.domain,
                "tld": url_result.tld,
                "is_ip_address": url_result.is_ip_address,
                "is_localhost": url_result.is_localhost,
                "is_suspicious_tld": url_result.is_suspicious_tld,
                "domain_length": url_result.domain_length,
                "has_port": url_result.has_port,
                "port": url_result.port,
                "path_depth": url_result.path_depth
            }
    except Exception as e:
        log.debug(f"URL analysis failed: {e}")
    
    manifest = None
    manifest_source = None
    
    parsed = urlparse(url)
    
    if "github.com" in parsed.netloc:
        manifest = fetch_package_json_from_github(url, timeout)
        if manifest:
            manifest_source = "github_package_json"
        
        github_info = fetch_github_repo_info(url, timeout)
        if github_info:
            profile["github_info"] = github_info
    
    if not manifest:
        if "npmjs.com" in parsed.netloc or "npm" in url:
            package_name = parsed.path.strip("/").split("/")[-1]
            manifest = fetch_from_npm(package_name, timeout)
            if manifest:
                manifest_source = "npm_registry"
                profile["package_info"] = {
                    "name": manifest.get("name"),
                    "version": manifest.get("version"),
                    "description": manifest.get("description"),
                    "license": manifest.get("license"),
                    "keywords": manifest.get("keywords", [])
                }
    
    if not manifest:
        manifest = fetch_manifest_from_url(url, timeout)
        if manifest:
            manifest_source = "remote_manifest"
    
    if not manifest:
        try:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json, */*"}
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if response.status_code == 200 and "json" in response.headers.get("Content-Type", ""):
                manifest = response.json()
                manifest_source = "direct_json"
        except requests.RequestException as e:
            profile["fetch_error"] = str(e)
    
    if manifest and isinstance(manifest, dict):
        profile["fetch_success"] = True
        profile["manifest"] = manifest
        profile["metadata"]["manifest_source"] = manifest_source
        
        profile["tool_names"] = extract_tool_names(manifest)
        profile["tool_count"] = len(profile["tool_names"])
        
        profile["permission_list"] = extract_permission_list(manifest)
        profile["has_authentication"] = check_has_authentication(manifest)
        profile["has_encryption"] = check_has_encryption(manifest)
        profile["declared_scope"] = extract_declared_scope(manifest)
        profile["server_type"] = determine_server_type(url, manifest)
        
        if not profile.get("package_info") and "name" in manifest:
            profile["package_info"] = {
                "name": manifest.get("name"),
                "version": manifest.get("version"),
                "description": manifest.get("description"),
                "license": manifest.get("license")
            }
        
        if not profile.get("github_info") and manifest.get("repository"):
            repo = manifest["repository"]
            if isinstance(repo, str):
                if repo.startswith("https://github.com"):
                    profile["github_info"] = {"url": repo}
                elif repo.startswith("github.com"):
                    profile["github_info"] = {"url": f"https://{repo}"}
    
    profile["profile_hash"] = compute_profile_hash(profile)
    
    return profile


def store_profile_in_registry(profile: Dict[str, Any], server_id: str) -> bool:
    try:
        import requests
        write_url = "http://127.0.0.1:8772/write"
        extended_metadata = {
            "mcp_profile": {
                "fetched_at": profile.get("fetched_at"),
                "tool_count": profile.get("tool_count"),
                "tool_names": profile.get("tool_names", [])[:50],
                "permission_list": profile.get("permission_list", []),
                "server_type": profile.get("server_type"),
                "has_authentication": profile.get("has_authentication"),
                "has_encryption": profile.get("has_encryption"),
                "declared_scope": profile.get("declared_scope"),
                "url_analysis": profile.get("url_analysis"),
                "profile_hash": profile.get("profile_hash"),
                "package_info": profile.get("package_info"),
                "github_info": profile.get("github_info"),
                "fetch_success": profile.get("fetch_success"),
                "metadata": profile.get("metadata", {})
            }
        }
        
        payload = {
            "table": "mcp_server_registry",
            "rows": {
                "server_id": server_id,
                "description": profile.get("package_info", {}).get("description") or profile.get("github_info", {}).get("description"),
                "extended_metadata": json.dumps(extended_metadata)
            }
        }
        
        response = requests.post(write_url, json=payload, timeout=10)
        return response.status_code in (200, 201, 204)
    except Exception as e:
        log.error(f"Failed to store profile in registry: {e}")
        return False


def enrich_server_profile(url: str, server_id: Optional[str] = None) -> Dict[str, Any]:
    profile = profile_mcp(url)
    
    if server_id and profile.get("fetch_success"):
        store_profile_in_registry(profile, server_id)
    
    return profile


def get_risk_indicators(profile: Dict[str, Any]) -> List[str]:
    indicators = []
    
    if not profile.get("fetch_success"):
        indicators.append("PROFILE_FETCH_FAILED")
    
    if profile.get("has_authentication") is False and profile.get("tool_count", 0) > 5:
        indicators.append("NO_AUTH_WITH_MULTIPLE_TOOLS")
    
    if profile.get("has_encryption") is False:
        indicators.append("NO_ENCRYPTION")
    
    url_analysis = profile.get("url_analysis", {})
    if url_analysis:
        if url_analysis.get("is_suspicious_tld"):
            indicators.append("SUSPICIOUS_TLD")
        if url_analysis.get("is_localhost"):
            indicators.append("LOCALHOST_URL")
        if url_analysis.get("is_ip_address"):
            indicators.append("IP_ADDRESS_URL")
    
    permissions = profile.get("permission_list", [])
    high_risk_perms = ["eval", "exec", "spawn", "secrets", "credentials", "keys", "passwords"]
    for perm in high_risk_perms:
        if perm in permissions:
            indicators.append(f"HIGH_RISK_PERMISSION_{perm.upper()}")
    
    tool_count = profile.get("tool_count", 0)
    if tool_count == 0:
        indicators.append("ZERO_TOOLS")
    elif tool_count > 100:
        indicators.append("EXCESSIVE_TOOLS")
    
    return indicators


if __name__ == "__main__":
    import sys
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    parser = argparse.ArgumentParser(description="ZO-SENTINEL MCP Server Profiler")
    parser.add_argument("url", help="MCP server URL to profile")
    parser.add_argument("--server-id", help="Server ID for registry update")
    parser.add_argument("--timeout", type=float, default=5.0, help="Request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    args = parser.parse_args()
    
    profile = profile_mcp(args.url, timeout=args.timeout)
    indicators = get_risk_indicators(profile)
    
    if args.json:
        output = {
            "profile": profile,
            "risk_indicators": indicators
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print(f"=== MCP Profile: {args.url} ===")
        print(f"Fetch Success: {profile['fetch_success']}")
        print(f"Server Type: {profile['server_type']}")
        print(f"Tool Count: {profile['tool_count']}")
        if profile['tool_names']:
            print(f"Tools: {', '.join(profile['tool_names'][:10])}")
            if len(profile['tool_names']) > 10:
                print(f"  ... and {len(profile['tool_names']) - 10} more")
        print(f"Permissions: {', '.join(profile['permission_list']) or 'None detected'}")
        print(f"Has Authentication: {profile['has_authentication']}")
        print(f"Has Encryption: {profile['has_encryption']}")
        print(f"Declared Scope: {profile['declared_scope'] or 'Not specified'}")
        
        if indicators:
            print(f"\n=== Risk Indicators ===")
            for indicator in indicators:
                print(f"  - {indicator}")
        
        if args.server_id:
            print(f"\nStoring profile for server_id: {args.server_id}")
            success = store_profile_in_registry(profile, args.server_id)
            print(f"Registry update: {'SUCCESS' if success else 'FAILED'}")