#!/usr/bin/env python3
"""
pattern_learner.py -- ZO-SENTINEL Pattern Learning Daemon.
Learns rejection patterns and false positive characteristics from analyst decisions.
Run interval: 86400s (daily).
"""
import os
import re
import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from typing import Dict, List, Any, Set, Tuple
import hashlib

import requests

log = logging.getLogger(__name__)

SERVICE_NAME = "pattern_learner"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
LEARNING_INTERVAL = 86400
MIN_PATTERN_OCCURRENCES = 3
FALSE_POSITIVE_TRUST_THRESHOLD = 0.4
ZO_SENTINEL_PATH = "/home/workspace/zo_sentinel"
KNOWLEDGE_BASE_PATH = os.path.join(ZO_SENTINEL_PATH, "KNOWLEDGE_BASE.md")
LEARNING_REPORT_PATH = os.path.join(ZO_SENTINEL_PATH, "LEARNING_REPORT.md")

PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

STOP_EVENT = False


def ws_query(sql: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """Execute a query via write_service query endpoint."""
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql, "params": params} if params else {"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result.get("data", [])
    except Exception as e:
        log.error(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed to {table}: {e}")
        return False


def send_heartbeat() -> bool:
    """Send heartbeat to service_health table."""
    return ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat()
    }])


def check_single_instance() -> bool:
    """Ensure only one instance runs at a time."""
    pid = os.getpid()
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance running with PID {old_pid}")
                return False
            except OSError:
                pass
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        return True
    except Exception as e:
        log.error(f"PID check failed: {e}")
        return False


def remove_pid_file():
    """Remove PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def get_rejection_decisions(days: int = 90) -> List[Dict[str, Any]]:
    """Fetch recent REJECTED decisions with server descriptions."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    sql = """
    SELECT 
        d.id,
        d.server_id,
        d.decision,
        d.decided_at,
        d.decided_by,
        r.name,
        r.description,
        r.url,
        r.trust_score
    FROM mcp_decisions d
    LEFT JOIN mcp_server_registry r ON d.server_id = r.server_id
    WHERE d.decision = 'REJECTED'
    AND d.decided_at >= %s
    ORDER BY d.decided_at DESC
    """
    return ws_query(sql, {"p1": cutoff})


def get_false_positive_decisions(days: int = 90) -> List[Dict[str, Any]]:
    """Fetch APPROVED decisions with low trust scores (false positives)."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    threshold = FALSE_POSITIVE_TRUST_THRESHOLD
    sql = """
    SELECT 
        d.id,
        d.server_id,
        d.decision,
        d.decided_at,
        d.decided_by,
        r.name,
        r.description,
        r.url,
        r.trust_score
    FROM mcp_decisions d
    LEFT JOIN mcp_server_registry r ON d.server_id = r.server_id
    WHERE d.decision = 'APPROVED'
    AND r.trust_score < %s
    AND d.decided_at >= %s
    ORDER BY d.decided_at DESC
    """
    return ws_query(sql, {"p1": threshold, "p2": cutoff})


def get_approval_decisions(days: int = 90) -> List[Dict[str, Any]]:
    """Fetch recent APPROVED decisions for comparison."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    sql = """
    SELECT 
        d.id,
        d.server_id,
        d.decision,
        d.decided_at,
        r.name,
        r.description,
        r.url,
        r.trust_score
    FROM mcp_decisions d
    LEFT JOIN mcp_server_registry r ON d.server_id = r.server_id
    WHERE d.decision = 'APPROVED'
    AND d.decided_at >= %s
    ORDER BY d.decided_at DESC
    """
    return ws_query(sql, {"p1": cutoff})


def normalize_text(text: str) -> str:
    """Normalize text for pattern extraction."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_ngrams(text: str, min_len: int = 3, max_len: int = 8) -> Set[str]:
    """Extract n-grams from text."""
    normalized = normalize_text(text)
    words = normalized.split()
    ngrams = set()
    for n in range(min_len, min(max_len + 1, len(words) + 1)):
        for i in range(len(words) - n + 1):
            ngram = ' '.join(words[i:i+n])
            if len(ngram) >= min_len:
                ngrams.add(ngram)
    return ngrams


def extract_keyword_patterns(description: str) -> Set[str]:
    """Extract significant keyword patterns from description."""
    if not description:
        return set()
    patterns = set()
    normalized = normalize_text(description)
    words = normalized.split()
    significant_words = {'api', 'server', 'tool', 'service', 'function', 'plugin', 
                         'model', 'data', 'file', 'auth', 'key', 'secret', 'token',
                         'cloud', 'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'k8s',
                         'sql', 'database', 'db', 'http', 'rest', 'graphql', 'grpc',
                         'python', 'node', 'java', 'javascript', 'typescript', 'go',
                         'lambda', 'function', 'webhook', 'callback', 'event', 'stream'}
    for word in words:
        if word in significant_words:
            patterns.add(word)
    if len(words) >= 2:
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            if any(w in significant_words for w in words[i:i+2]):
                patterns.add(bigram)
    suspicious_keywords = ['hack', 'exploit', 'inject', 'bypass', 'crack', 'steal',
                           'keylog', 'sniff', 'scrape', 'harvest', 'spam', 'phish',
                           'malicious', 'trojan', 'backdoor', 'rootkit']
    for word in words:
        if word in suspicious_keywords:
            patterns.add(word)
    return patterns


def analyze_rejection_patterns(rejections: List[Dict[str, Any]]) -> Dict[str, int]:
    """Analyze rejection decisions to find common patterns."""
    pattern_counts = Counter()
    description_ngrams = Counter()
    url_patterns = Counter()
    name_patterns = Counter()
    for decision in rejections:
        description = decision.get('description', '') or ''
        name = decision.get('name', '') or ''
        url = decision.get('url', '') or ''
        if description:
            keywords = extract_keyword_patterns(description)
            for kw in keywords:
                pattern_counts[kw] += 1
            ngrams = extract_ngrams(description, min_len=4, max_len=6)
            for ngram in ngrams:
                if len(ngram) >= 5:
                    description_ngrams[ngram] += 1
        if name:
            name_normalized = normalize_text(name)
            words = name_normalized.split()
            for word in words:
                if len(word) >= 4:
                    name_patterns[word] += 1
        if url:
            url_parts = re.findall(r'(?:github\.com|gitlab\.com|raw\.githubusercontent|npm\.js|pypi\.org|docker\.hub)\S+', url)
            for part in url_parts[:3]:
                url_patterns[part] += 1
    significant_patterns = {}
    for pattern, count in pattern_counts.most_common(50):
        if count >= MIN_PATTERN_OCCURRENCES:
            significant_patterns[pattern] = count
    for ngram, count in description_ngrams.most_common(100):
        if count >= MIN_PATTERN_OCCURRENCES and len(ngram) >= 5:
            significant_patterns[f"ngram:{ngram}"] = count
    for word, count in name_patterns.most_common(30):
        if count >= MIN_PATTERN_OCCURRENCES:
            significant_patterns[f"name:{word}"] = count
    for url_part, count in url_patterns.most_common(20):
        if count >= 2:
            significant_patterns[f"url:{url_part}"] = count
    return significant_patterns


def analyze_false_positive_patterns(false_positives: List[Dict[str, Any]], 
                                     approvals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze false positives to understand what led to wrong approvals."""
    fp_characteristics = {
        'low_trust_approved_descriptions': [],
        'common_fp_keywords': Counter(),
        'average_trust_score': 0,
        'count': len(false_positives)
    }
    if not false_positives:
        return fp_characteristics
    trust_scores = []
    for fp in false_positives:
        desc = fp.get('description', '') or ''
        trust = fp.get('trust_score', 0)
        trust_scores.append(trust)
        if desc:
            fp_characteristics['low_trust_approved_descriptions'].append(desc[:200])
            keywords = extract_keyword_patterns(desc)
            for kw in keywords:
                fp_characteristics['common_fp_keywords'][kw] += 1
    fp_characteristics['average_trust_score'] = sum(trust_scores) / len(trust_scores) if trust_scores else 0
    fp_characteristics['min_trust_score'] = min(trust_scores) if trust_scores else 0
    fp_characteristics['max_trust_score'] = max(trust_scores) if trust_scores else 0
    significant_fp_keywords = {
        kw: count for kw, count in 
        fp_characteristics['common_fp_keywords'].most_common(20)
        if count >= 2
    }
    fp_characteristics['significant_keywords'] = significant_fp_keywords
    return fp_characteristics


def load_current_knowledge_base() -> Dict[str, Any]:
    """Load current knowledge base to find existing patterns."""
    patterns = {
        'LEARNED_REJECTION_PATTERNS': [],
        'LEARNED_FALSE_POSITIVE_PATTERNS': [],
        'last_updated': None
    }
    if not os.path.exists(KNOWLEDGE_BASE_PATH):
        return patterns
    try:
        with open(KNOWLEDGE_BASE_PATH, "r") as f:
            content = f.read()
        rejection_match = re.search(r'LEARNED_REJECTION_PATTERNS\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if rejection_match:
            patterns_text = rejection_match.group(1)
            items = re.findall(r"['\"]([^'\"]+)['\"]", patterns_text)
            patterns['LEARNED_REJECTION_PATTERNS'] = items
        fp_match = re.search(r'LEARNED_FALSE_POSITIVE_PATTERNS\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if fp_match:
            patterns_text = fp_match.group(1)
            items = re.findall(r"['\"]([^'\"]+)['\"]", patterns_text)
            patterns['LEARNED_FALSE_POSITIVE_PATTERNS'] = items
        updated_match = re.search(r'LEARNED_PATTERNS_UPDATED:\s*(.+)', content)
        if updated_match:
            patterns['last_updated'] = updated_match.group(1).strip()
    except Exception as e:
        log.error(f"Failed to load knowledge base: {e}")
    return patterns


def update_knowledge_base(rejection_patterns: Dict[str, int], 
                          fp_patterns: Dict[str, Any]) -> None:
    """Update KNOWLEDGE_BASE.md with learned patterns."""
    timestamp = datetime.utcnow().isoformat()
    current_kb = load_current_knowledge_base()
    existing_rejection = set(current_kb.get('LEARNED_REJECTION_PATTERNS', []))
    existing_fp = set(current_kb.get('LEARNED_FALSE_POSITIVE_PATTERNS', []))
    new_rejection_patterns = [p for p in rejection_patterns.keys() if p not in existing_rejection]
    new_fp_patterns = [kw for kw in fp_patterns.get('significant_keywords', {}).keys() 
                       if kw not in existing_fp]
    all_rejection = list(existing_rejection) + new_rejection_patterns
    all_fp = list(existing_fp) + new_fp_patterns
    kb_lines = []
    if os.path.exists(KNOWLEDGE_BASE_PATH):
        with open(KNOWLEDGE_BASE_PATH, "r") as f:
            kb_lines = f.readlines()
    else:
        kb_lines = ["# ZO-SENTINEL Knowledge Base\n\n"]
    new_content_lines = []
    for line in kb_lines:
        if 'LEARNED_REJECTION_PATTERNS' in line and '=' in line:
            continue
        if 'LEARNED_FALSE_POSITIVE_PATTERNS' in line and '=' in line:
            continue
        if 'LEARNED_PATTERNS_UPDATED:' in line:
            continue
        new_content_lines.append(line)
    if not any('# LEARNED PATTERNS' in line for line in new_content_lines):
        new_content_lines.append("\n## LEARNED PATTERNS\n")
    rejection_str = "[\n    " + ",\n    ".join(f"'{p}'" for p in all_rejection[:100]) + "\n  ]"
    fp_str = "[\n    " + ",\n    ".join(f"'{kw}'" for kw in all_fp[:50]) + "\n  ]"
    new_content_lines.append(f"LEARNED_REJECTION_PATTERNS = {rejection_str}\n")
    new_content_lines.append(f"LEARNED_FALSE_POSITIVE_PATTERNS = {fp_str}\n")
    new_content_lines.append(f"LEARNED_PATTERNS_UPDATED: {timestamp}\n")
    try:
        with open(KNOWLEDGE_BASE_PATH, "w") as f:
            f.writelines(new_content_lines)
        log.info(f"Updated KNOWLEDGE_BASE.md with {len(new_rejection_patterns)} new rejection patterns")
    except Exception as e:
        log.error(f"Failed to update knowledge base: {e}")


def generate_learning_report(rejection_patterns: Dict[str, int],
                             fp_patterns: Dict[str, Any],
                             rejection_count: int,
                             fp_count: int) -> None:
    """Generate LEARNING_REPORT.md with analysis summary."""
    timestamp = datetime.utcnow().isoformat()
    report = f"""# ZO-SENTINEL Pattern Learning Report
Generated: {timestamp}

## Summary
- Rejection decisions analyzed: {rejection_count}
- False positive decisions analyzed: {fp_count}
- Rejection patterns discovered: {len([p for p in rejection_patterns.keys() if not p.startswith(('ngram:', 'name:', 'url:'))])}
- N-gram patterns discovered: {len([p for p in rejection_patterns.keys() if p.startswith('ngram:')])}"
- False positive keywords: {len(fp_patterns.get('significant_keywords', {}))}

## Top Rejection Patterns
"""
    top_patterns = sorted(rejection_patterns.items(), key=lambda x: x[1], reverse=True)[:30]
    for pattern, count in top_patterns:
        report += f"- **{pattern}**: found in {count} rejections\n"
    report += f"""
## False Positive Characteristics
- Total false positives: {fp_count}
- Average trust score of false positives: {fp_patterns.get('average_trust_score', 0):.3f}
- Min trust score: {fp_patterns.get('min_trust_score', 0):.3f}
- Max trust score: {fp_patterns.get('max_trust_score', 0):.3f}

### Common False Positive Keywords
"""
    fp_keywords = fp_patterns.get('significant_keywords', {})
    for kw, count in sorted(fp_keywords.items(), key=lambda x: x[1], reverse=True)[:15]:
        report += f"- **{kw}**: {count} occurrences\n"
    report += """
## Recommendations

### For Rejection Detection
Watch for servers with descriptions containing the above rejection patterns.
These patterns have been found in 3+ analyst rejections and may indicate untrustworthy servers.

### For False Positive Reduction
When approving servers with low trust scores, pay extra attention to servers
containing the common false positive keywords. Consider requiring additional
verification for servers matching these characteristics.

---
*This report is auto-generated by pattern_learner.py*
"""
    try:
        with open(LEARNING_REPORT_PATH, "w") as f:
            f.write(report)
        log.info(f"Generated LEARNING_REPORT.md")
    except Exception as e:
        log.error(f"Failed to write learning report: {e}")


def write_mesh_event(event_type: str, details: Dict[str, Any]) -> bool:
    """Write an event to mesh_events table."""
    event = {
        "event_type": event_type,
        "source": SERVICE_NAME,
        "timestamp": datetime.utcnow().isoformat(),
        "details": str(details)
    }
    return ws_write("mesh_events", [event])


def run_learning_cycle() -> Dict[str, Any]:
    """Execute one complete learning cycle."""
    log.info("Starting pattern learning cycle...")
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "rejection_patterns_found": 0,
        "false_positive_patterns_found": 0,
        "knowledge_base_updated": False,
        "report_generated": False,
        "errors": []
    }
    try:
        rejections = get_rejection_decisions(days=90)
        log.info(f"Found {len(rejections)} rejection decisions")
        fp_decisions = get_false_positive_decisions(days=90)
        approvals = get_approval_decisions(days=90)
        log.info(f"Found {len(fp_decisions)} false positive decisions, {len(approvals)} total approvals")
        rejection_patterns = analyze_rejection_patterns(rejections)
        log.info(f"Discovered {len(rejection_patterns)} rejection patterns")
        results["rejection_patterns_found"] = len(rejection_patterns)
        fp_patterns = analyze_false_positive_patterns(fp_decisions, approvals)
        log.info(f"Discovered {len(fp_patterns.get('significant_keywords', {}))} false positive keywords")
        results["false_positive_patterns_found"] = len(fp_patterns.get('significant_keywords', {}))
        if rejection_patterns:
            update_knowledge_base(rejection_patterns, fp_patterns)
            results["knowledge_base_updated"] = True
        generate_learning_report(
            rejection_patterns, 
            fp_patterns,
            len(rejections),
            len(fp_decisions)
        )
        results["report_generated"] = True
        pattern_summary = {
            "total_patterns": len(rejection_patterns),
            "top_5": [p for p, _ in sorted(rejection_patterns.items(), key=lambda x: x[1], reverse=True)[:5]]
        }
        write_mesh_event("pattern_learning_completed", {
            "patterns_discovered": len(rejection_patterns),
            "rejections_analyzed": len(rejections),
            "false_positives_analyzed": len(fp_decisions),
            "summary": pattern_summary
        })
        ws_write("mesh_events", [{
            "event_type": "learned_patterns",
            "source": SERVICE_NAME,
            "timestamp": datetime.utcnow().isoformat(),
            "details": f"Discovered {len(rejection_patterns)} rejection patterns from {len(rejections)} rejections"
        }])
        log.info("Pattern learning cycle completed successfully")
    except Exception as e:
        log.error(f"Learning cycle failed: {e}")
        results["errors"].append(str(e))
    return results


def heartbeat_loop():
    """Main heartbeat and scheduling loop."""
    global STOP_EVENT
    last_learning = datetime.utcfromtimestamp(0)
    log.info(f"Starting {SERVICE_NAME} heartbeat loop...")
    while not STOP_EVENT:
        try:
            send_heartbeat()
            now = datetime.utcnow()
            if (now - last_learning).total_seconds() >= LEARNING_INTERVAL:
                run_learning_cycle()
                last_learning = now
        except Exception as e:
            log.error(f"Heartbeat loop error: {e}")
        for _ in range(min(HEARTBEAT_INTERVAL, 10)):
            if STOP_EVENT:
                break
            import time
            time.sleep(1)


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global STOP_EVENT
    log.info(f"Received signal {signum}, shutting down...")
    STOP_EVENT = True


def run():
    """Main entry point for pattern learner daemon."""
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    if not check_single_instance():
        log.error("Cannot start: another instance is running")
        return
    log.info(f"Starting {SERVICE_NAME}...")
    try:
        heartbeat_loop()
    except Exception as e:
        log.error(f"Fatal error: {e}")
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run()