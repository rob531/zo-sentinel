import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
SERVICE_NAME = "signal_v2_discrimination_boost"
PORT = 0
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = "/tmp/signal_v2_discrimination_boost.log"
POLL_SECS = 3600
MAX_SCORE = 100.0
MIN_SCORE = 0.0

def ws_query(sql):
    import requests
    resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_write(table, rows, wait=True):
    import requests
    resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": wait}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_execute(sql):
    import requests
    resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def check_single_instance():
    import os
    import sys
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = f.read().strip()
        if existing and os.path.exists(f"/proc/{existing}"):
            print(f"Already running as PID {existing}")
            sys.exit(0)
    with open(PID_FILE, "w") as f:
        f.write(pid)

def remove_pid_file():
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    remove_pid_file()
    import sys
    sys.exit(0)

def log(msg):
    import datetime
    ts = datetime.datetime.utcnow().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

def send_heartbeat():
    try:
        ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": __import__('datetime').datetime.utcnow().isoformat()})
    except Exception as e:
        log(f"Heartbeat failed: {e}")

def get_score_band(score):
    if score >= 90:
        return "exceptional"
    elif score >= 75:
        return "high"
    elif score >= 60:
        return "moderate"
    elif score >= 40:
        return "low"
    else:
        return "minimal"

def compute_permission_weight(permission_scope):
    if not permission_scope:
        return 0.0
    scope = str(permission_scope).lower()
    if scope in ["none", "minimal", "read-only"]:
        return 1.0
    elif scope in ["low", "limited"]:
        return 0.75
    elif scope in ["medium", "standard", "moderate"]:
        return 0.5
    elif scope in ["high", "elevated"]:
        return 0.25
    elif scope in ["admin", "full", "privileged", "dangerous"]:
        return 0.0
    return 0.5

def score_publisher_verified(publisher_verified, registry_source):
    if publisher_verified in [True, "true", "True", 1, "1"]:
        return 25.0
    registry = str(registry_source or "").lower()
    if registry in ["npm official", "github", "smithery", "anthropic"]:
        return 15.0
    return 0.0

def score_dependency_count(dependency_count):
    if not dependency_count:
        return 0.0
    try:
        dc = int(dependency_count)
    except (ValueError, TypeError):
        dc = 0
    if dc == 0:
        return 20.0
    elif dc <= 5:
        return 18.0
    elif dc <= 10:
        return 15.0
    elif dc <= 20:
        return 12.0
    elif dc <= 50:
        return 8.0
    else:
        return 4.0

def score_download_count(download_count):
    if not download_count:
        return 0.0
    try:
        dl = int(download_count)
    except (ValueError, TypeError):
        dl = 0
    if dl == 0:
        return 0.0
    elif dl >= 1000000:
        return 20.0
    elif dl >= 100000:
        return 18.0
    elif dl >= 10000:
        return 15.0
    elif dl >= 1000:
        return 12.0
    elif dl >= 100:
        return 8.0
    else:
        return 5.0

def score_stars(stars):
    if not stars:
        return 0.0
    try:
        s = int(stars)
    except (ValueError, TypeError):
        s = 0
    if s == 0:
        return 0.0
    elif s >= 10000:
        return 20.0
    elif s >= 1000:
        return 17.0
    elif s >= 500:
        return 14.0
    elif s >= 100:
        return 11.0
    elif s >= 50:
        return 8.0
    elif s >= 10:
        return 5.0
    else:
        return 3.0

def score_age_days(age_days):
    if not age_days:
        return 10.0
    try:
        ad = int(age_days)
    except (ValueError, TypeError):
        ad = 0
    if ad >= 1825:
        return 20.0
    elif ad >= 1095:
        return 18.0
    elif ad >= 730:
        return 16.0
    elif ad >= 365:
        return 14.0
    elif ad >= 180:
        return 12.0
    elif ad >= 90:
        return 10.0
    elif ad >= 30:
        return 7.0
    elif ad >= 7:
        return 4.0
    else:
        return 2.0

def score_registry_source_tier(registry_source):
    registry = str(registry_source or "").lower()
    if registry in ["npm official", "anthropic"]:
        return 20.0
    elif registry in ["github"]:
        return 17.0
    elif registry in ["smithery"]:
        return 14.0
    elif registry in ["mcp.so", "mcp directory"]:
        return 10.0
    elif registry:
        return 6.0
    return 3.0

def score_security_indicators(metadata):
    score = 0.0
    description = str(metadata.get("description", "") or "").lower()
    url = str(metadata.get("url", "") or "").lower()
    
    positive_indicators = [
        "security", "audit", "verified", "official", "trusted",
        "soc2", "iso27001", "gdpr", "hipaa", "fedramp"
    ]
    negative_indicators = [
        "unverified", "untrusted", "suspicious", "malicious",
        "phishing", "fake", " counterfeit"
    ]
    
    for indicator in positive_indicators:
        if indicator in description or indicator in url:
            score += 5.0
    
    for indicator in negative_indicators:
        if indicator in description or indicator in url:
            score -= 10.0
    
    return max(0.0, min(score, 25.0))

def compute_score(metadata):
    permission_scope = metadata.get("permission_scope")
    publisher_verified = metadata.get("publisher_verified")
    registry_source = metadata.get("registry_source")
    dependency_count = metadata.get("dependency_count")
    download_count = metadata.get("download_count")
    stars = metadata.get("stars")
    age_days = metadata.get("age_days")
    url = metadata.get("url")
    description = metadata.get("description")
    
    perm_weight = compute_permission_weight(permission_scope)
    perm_score = perm_weight * 30.0
    
    pub_score = score_publisher_verified(publisher_verified, registry_source)
    dep_score = score_dependency_count(dependency_count)
    dl_score = score_download_count(download_count)
    star_score = score_stars(stars)
    age_score = score_age_days(age_days)
    reg_score = score_registry_source_tier(registry_source)
    
    security_boost = score_security_indicators(metadata)
    
    total_score = (
        perm_score * 0.25 +
        pub_score * 0.15 +
        dep_score * 0.12 +
        dl_score * 0.15 +
        star_score * 0.10 +
        age_score * 0.10 +
        reg_score * 0.08 +
        security_boost * 0.05
    )
    
    final_score = round(max(MIN_SCORE, min(MAX_SCORE, total_score)), 2)
    
    detail = {
        "permission_scope_score": round(perm_score, 2),
        "publisher_verified_score": round(pub_score, 2),
        "dependency_count_score": round(dep_score, 2),
        "download_count_score": round(dl_score, 2),
        "stars_score": round(star_score, 2),
        "age_days_score": round(age_score, 2),
        "registry_source_score": round(reg_score, 2),
        "security_boost_score": round(security_boost, 2),
        "permission_weight": perm_weight,
        "raw_metadata": {
            "permission_scope": permission_scope,
            "publisher_verified": publisher_verified,
            "registry_source": registry_source,
            "dependency_count": dependency_count,
            "download_count": download_count,
            "stars": stars,
            "age_days": age_days
        }
    }
    
    return final_score, detail

def compute_batch_scores(servers_with_metadata):
    results = []
    seen_scores = set()
    for server_id, metadata in servers_with_metadata:
        score, detail = compute_score(metadata)
        results.append({
            "server_id": server_id,
            "score": score,
            "detail": detail,
            "band": get_score_band(score)
        })
        seen_scores.add(score)
    return results, seen_scores

def ensure_boost_table():
    ws_execute("""
        CREATE TABLE IF NOT EXISTS signal_v2_discrimination_boost (
            server_id VARCHAR,
            score DOUBLE,
            permission_scope_score DOUBLE,
            publisher_verified_score DOUBLE,
            dependency_count_score DOUBLE,
            download_count_score DOUBLE,
            stars_score DOUBLE,
            age_days_score DOUBLE,
            registry_source_score DOUBLE,
            security_boost_score DOUBLE,
            computed_at VARCHAR
        )
    """)

def write_boost_scores(results):
    if not results:
        return
    rows = []
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    for r in results:
        detail = r.get("detail", {})
        rows.append({
            "server_id": r["server_id"],
            "score": r["score"],
            "permission_scope_score": detail.get("permission_scope_score", 0.0),
            "publisher_verified_score": detail.get("publisher_verified_score", 0.0),
            "dependency_count_score": detail.get("dependency_count_score", 0.0),
            "download_count_score": detail.get("download_count_score", 0.0),
            "stars_score": detail.get("stars_score", 0.0),
            "age_days_score": detail.get("age_days_score", 0.0),
            "registry_source_score": detail.get("registry_source_score", 0.0),
            "security_boost_score": detail.get("security_boost_score", 0.0),
            "computed_at": now
        })
    ws_write("signal_v2_discrimination_boost", rows)

def get_fingerprint_metadata():
    query = """
        SELECT 
            r.server_id,
            r.registry_source,
            r.url,
            r.description,
            COALESCE(r.scan_count, 0) as scan_count,
            COALESCE(r.trust_score, 50.0) as trust_score,
            COALESCE(rr.risk_tier, 'medium') as risk_tier,
            COALESCE(rr.threat_count, 0) as threat_count
        FROM mcp_server_registry r
        LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id
        WHERE r.server_id IS NOT NULL
    """
    try:
        result = ws_query(query)
        return result.get("rows", [])
    except Exception as e:
        log(f"Failed to query fingerprint metadata: {e}")
        return []

def enrich_with_npm_metadata(servers):
    try:
        import requests
        for server in servers:
            url = server.get("url", "")
            if "npmjs.com" in url or "npm.im" in url:
                pkg_name = extract_npm_package_name(url)
                if pkg_name:
                    npm_data = fetch_npm_metadata(pkg_name)
                    if npm_data:
                        server["download_count"] = npm_data.get("downloads")
                        server["stars"] = npm_data.get("stars")
                        server["dependency_count"] = npm_data.get("dependencies", {}).get("direct", 0)
                        server["age_days"] = npm_data.get("age_days")
                        server["publisher_verified"] = npm_data.get("publisher_verified")
    except Exception as e:
        log(f"Failed to enrich with npm metadata: {e}")
    return servers

def extract_npm_package_name(url):
    import re
    if not url:
        return None
    patterns = [
        r"npmjs\.com/(?:package/)?(@[^/]+/[^/]+)",
        r"npmjs\.com/(?:package/)?([^/]+)",
        r"npm\.im/(@[^/]+/[^/]+)",
        r"npm\.im/([^/]+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def fetch_npm_metadata(package_name):
    import requests
    try:
        resp = requests.get(
            f"https://registry.npmjs.org/{package_name}",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            from datetime import datetime
            time = data.get("time", {})
            created = time.get("created", "")
            modified = time.get("modified", "")
            age_days = 0
            if created:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_days = (datetime.utcnow() - created_dt.replace(tzinfo=None)).days
                except:
                    pass
            return {
                "downloads": data.get("downloads", 0),
                "stars": data.get("stars", 0),
                "dependencies": data.get("dependencies", {}),
                "age_days": age_days,
                "publisher_verified": data.get("publisher", {}).get("verified", False)
            }
    except Exception as e:
        pass
    return None

def enrich_with_github_metadata(servers):
    try:
        import requests
        import re
        for server in servers:
            url = server.get("url", "")
            if "github.com" in url:
                match = re.search(r"github\.com/([^/]+)/([^/\s]+)", url)
                if match:
                    owner, repo = match.groups()
                    gh_data = fetch_github_metadata(owner, repo)
                    if gh_data:
                        server["stars"] = gh_data.get("stars")
                        server["dependency_count"] = gh_data.get("dependency_count")
    except Exception as e:
        log(f"Failed to enrich with github metadata: {e}")
    return servers

def fetch_github_metadata(owner, repo):
    import requests
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "stars": data.get("stargazers_count", 0),
                "dependency_count": 0
            }
    except:
        pass
    return None

def compute_fingerprint_discrimination():
    servers = get_fingerprint_metadata()
    log(f"Fetched {len(servers)} servers from registry")
    
    servers = enrich_with_npm_metadata(servers)
    servers = enrich_with_github_metadata(servers)
    
    servers_with_metadata = []
    for server in servers:
        metadata = {
            "permission_scope": server.get("permission_scope"),
            "publisher_verified": server.get("publisher_verified"),
            "registry_source": server.get("registry_source"),
            "dependency_count": server.get("dependency_count"),
            "download_count": server.get("download_count"),
            "stars": server.get("stars"),
            "age_days": server.get("age_days"),
            "url": server.get("url"),
            "description": server.get("description")
        }
        servers_with_metadata.append((server["server_id"], metadata))
    
    results, seen_scores = compute_batch_scores(servers_with_metadata)
    
    log(f"Computed scores for {len(results)} servers with {len(seen_scores)} distinct values")
    
    return results, seen_scores

def run():
    import signal
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log("Starting signal_v2_discrimination_boost")
    
    ensure_boost_table()
    
    results, seen_scores = compute_fingerprint_discrimination()
    
    write_boost_scores(results)
    
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": __import__('datetime').datetime.utcnow().isoformat()})
    
    log(f"Discrimination boost complete: {len(seen_scores)} distinct scores across {len(results)} inputs")
    
    if len(seen_scores) < 20:
        log(f"WARNING: Only {len(seen_scores)} distinct scores - target is >20")
    else:
        log(f"SUCCESS: {len(seen_scores)} distinct scores achieved (>20 target)")
    
    remove_pid_file()
    
    return results, seen_scores

if __name__ == "__main__":
    run()