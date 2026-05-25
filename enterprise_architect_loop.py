#!/usr/bin/env python3
"""
enterprise_architect_loop.py -- Deterministic maturity evaluation with living intelligence.

RULE SOURCES (in order of authority):
  1. MATURITY_REGISTRY          -- structural gaps (CTO intent)
  2. SENTINEL_ROADMAP.md        -- strategic initiatives
  3. AI_RESEARCH_KNOWLEDGE.md   -- nightly research scout -> novel patterns
  4. world_articles             -- live threat intel -> new detectors
  5. GENERATION_FAILURES.md     -- failed builds -> auto-compressed re-queues  <-- NEW v2
  6. mesh_memory                -- what Sentinel has actually observed

v2 changes:
  - Source 5: reads GENERATION_FAILURES.md, detects 'empty response' failures
    (symptom of context-window overflow), uses local Qwen to compress bloated
    descriptions to <2000 chars, re-queues as compact variant.
  - PASS 0 runs before PASS 1 so compression fixes unblock the registry queue.
"""
import os, json, glob, re, logging, requests, time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR   = Path('/home/workspace/zo_sentinel')
DIRECTIVE_DIR = PROJECT_DIR / 'directives'
WRITE_SERVICE = 'http://127.0.0.1:8772'
OLLAMA_URL    = 'http://localhost:11434'

RESEARCH_KB   = Path('/home/workspace') / 'AI_RESEARCH_KNOWLEDGE.md'
ROADMAP       = PROJECT_DIR / 'SENTINEL_ROADMAP.md'
FAILURES_MD   = PROJECT_DIR / 'GENERATION_FAILURES.md'
PAUSED_JSON   = DIRECTIVE_DIR / 'paused_files.json'

# Description length that triggers context-window compression
COMPRESSION_THRESHOLD = 3000  # chars

log = logging.getLogger('architect')


MATURITY_REGISTRY = {
    'schema.py':                    {'pillar': 'FOUNDATION', 'skip': True},
    'signal_analyser.py':           {'pillar': 'FOUNDATION', 'skip': True},
    'trust_synthesiser.py':         {'pillar': 'FOUNDATION', 'skip': True},
    'registry_api.py':              {'pillar': 'FOUNDATION', 'skip': True},
    'search_api.py':                {'pillar': 'FOUNDATION', 'skip': True},
    'approval_workflow.py':         {'pillar': 'FOUNDATION', 'skip': True},
    'compliance_export_service.py': {'pillar': 'ETL',        'skip': True},
    'mcp_scanner.py':               {'pillar': 'FOUNDATION', 'skip': True},

    'email_guid_auth.py': {
        'pillar': 'RBAC', 'priority': 0.99, 'phase': '17', 'complexity': 'high',
        'description': (
            'Email GUID auth service port 8775. FastAPI. '
            'POST /api/send-approval-email {mcp_name, submission_id, requested_by, admin_email}: '
            'generate uuid4 token_id, ws_write auth_tokens {token_id, action=approve_mcp, mcp_name, '
            'submission_id, admin_email, expires_at=now()+24h, used=False}, '
            'POST https://api.zo.computer/zo/notify {to, subject, body with approve/deny links}. '
            'GET /api/validate?token=: ws_query auth_tokens WHERE token_id=? AND used=FALSE AND expires_at>now(). '
            'POST /api/consume {token_id}: ws_write used=TRUE. '
            'GET /health. All DB via ws_write/ws_query to write_service:8772. No duckdb. '
            'uuid4 from uuid module. run() uvicorn. Heartbeat. '
            'SMOKE TEST CONTRACT: must contain uuid4 and ws_write.'
        ),
    },
    'manual_override_api.py': {
        'pillar': 'RBAC', 'priority': 0.97, 'phase': '17', 'complexity': 'high',
        'depends_on': ['email_guid_auth.py'],
        'description': (
            'Admin override API port 8776. Auth via ZO_ADMIN_TOKEN env var OR guid: prefixed token. '
            'POST /api/override {mcp_name, new_trust_score, status, reason, admin_token}: '
            'idempotency check ws_query mcp_risk_register, then ws_write mcp_risk_register + audit_log. '
            'GET /api/overrides. GET /health. run() uvicorn. Heartbeat.'
        ),
    },
    'rbac_middleware.py': {
        'pillar': 'RBAC', 'priority': 0.88, 'phase': '20', 'complexity': 'high',
        'depends_on': ['email_guid_auth.py'],
        'description': (
            'RBAC middleware. Roles: ADMIN, ANALYST, READONLY in auth_tokens.action. '
            'FastAPI dependency verify_role(required_role): validate token via ws_query, '
            'return 403 if insufficient. Apply ADMIN to manual_override_api, ANALYST to approval_workflow. '
            'GET /api/roles. No duckdb. write_service:8772. Heartbeat.'
        ),
    },
    'nl_query_engine.py': {
        'pillar': 'NLP', 'priority': 0.85, 'phase': '20', 'complexity': 'high',
        'description': (
            'NL-to-SQL engine port 8784. POST /api/nl-query {question}: send to inference_router '
            'task_type=structure with schema context for mcp_server_registry, mcp_threat_associations, '
            'mcp_risk_register. Validate SQL (SELECT only, no DROP/DELETE). Execute via ws_query. '
            'Return {question, sql, results, row_count}. Cache last 20 queries. GET /health. Heartbeat.'
        ),
    },
    'advanced_filter_api.py': {
        'pillar': 'ETL', 'priority': 0.96, 'phase': '17', 'complexity': 'high',
        'description': (
            'OLAP faceted discovery API port 8777. '
            'POST /api/discover {trust_score_lt, trust_score_gt, status, verdict(list), '
            'has_threats(bool), has_cve(bool), registry_source, risk_tier, age_days_lt}: '
            'build safe parameterised WHERE clause, JOIN mcp_server_registry+mcp_risk_register. '
            'GET /api/discover/facets returns breakdown by verdict/risk_tier/registry_source. '
            'All via ws_query:8772. No duckdb. run() uvicorn. Heartbeat.'
        ),
    },
    'forensic_detail_api.py': {
        'pillar': 'UXP/IA', 'priority': 0.93, 'phase': '18', 'complexity': 'high',
        'description': (
            'Forensic lifecycle API port 8779. GET /api/forensics/{mcp_name}: '
            '404 if not found. Sequential ws_query: registry+signals+risk+audit_log+threats+attestations. '
            'Assemble chronological timeline {timestamp, event_type, description, data}. '
            'Return {server_id, name, current_verdict, trust_score, timeline, summary}. '
            'GET /api/forensics/{mcp_name}/timeline?event_type= filter. GET /health. run() uvicorn. Heartbeat.'
        ),
    },
    'mcp_detail_view.html': {
        'pillar': 'UXP/IA', 'priority': 0.91, 'phase': '19', 'complexity': 'medium',
        'depends_on': ['forensic_detail_api.py'],
        'description': (
            'Forensic drill-down HTML. URLSearchParams reads ?mcp=name. '
            'fetch /api/forensics/{name}. 404 panel if not found. '
            'Timeline rows: timestamp + colour-coded badge (THREAT=red, SIGNAL=cyan, ADMIN=orange, DISCOVERED=grey). '
            'Trust score gauge. Dark brutalist #0d0f14/#00e5ff. Monospace. No external deps.'
        ),
    },
    'rule_engine_api.py': {
        'pillar': 'BRMS', 'priority': 0.82, 'phase': '21', 'complexity': 'high',
        'description': (
            'Externalised BRMS port 8785. Rules in mcp_policy_rules table. '
            'GET /api/rules. POST /api/rules {rule_name, rule_type, pattern, action, description}: ws_write. '
            'PUT/DELETE /api/rules/{id}. '
            'POST /api/rules/evaluate {server_id}: run all active rules, return {rules_matched, recommended_action}. '
            'Rule types: PERMISSION_BLOCK, SIGNAL_THRESHOLD, VERDICT_BLOCK, AGE_GATE, SOURCE_FILTER. '
            'GET /health. run() uvicorn. Heartbeat.'
        ),
    },
    'servicenow_connector.py': {
        'pillar': 'SOA', 'priority': 0.78, 'phase': '22', 'complexity': 'high',
        'description': (
            'ServiceNow connector daemon. ws_query mcp_submissions WHERE status=pending, '
            'POST to SNOW /api/now/table/sc_req_item. Poll SNOW for decisions every 300s. '
            'On decision: ws_write mcp_decisions + mcp_submissions status update. '
            'Env: SNOW_INSTANCE, SNOW_USER, SNOW_PASSWORD. Skip gracefully if not set. '
            'Port 8786. GET /health. Heartbeat.'
        ),
    },
    'aidr_connector.py': {
        'pillar': 'SOA', 'priority': 0.75, 'phase': '22', 'complexity': 'high',
        'depends_on': ['servicenow_connector.py'],
        'description': (
            'CrowdStrike AiDr connector. ws_query mcp_decisions WHERE decision=ALLOW AND synced_to_aidr IS NULL. '
            'POST to Falcon API MCP allowlist. On REVOKED: DELETE from allowlist. '
            'POST /webhook/aidr-alert receives anomaly signals, ws_write mcp_threat_associations. '
            'Env: CROWDSTRIKE_CLIENT_ID, CROWDSTRIKE_CLIENT_SECRET. Port 8787. GET /health. Heartbeat.'
        ),
    },
}


# ---------------------------------------------------------------------------
# Source 5: GENERATION_FAILURES.md auto-compressor
# ---------------------------------------------------------------------------

def read_generation_failures():
    """
    Source 5: Parse GENERATION_FAILURES.md for failed builds.
    Returns list of {task_name, failure_type, description} dicts.
    Failure types we care about:
      - 'empty_response' : MiniMax returned empty -- context window overflow
      - 'too_short'      : model returned stub -- under-specified prompt
    """
    if not FAILURES_MD.exists():
        return []

    content = FAILURES_MD.read_text()
    failures = []
    current = {}

    for line in content.split('\n'):
        # New failure block
        m = re.match(r'##\s+\d{4}-\d{2}-\d{2}.+\|\s*(.+)', line)
        if m:
            if current.get('task_name'):
                failures.append(current)
            current = {'task_name': m.group(1).strip(), 'failure_type': 'unknown', 'description': ''}
        elif 'empty' in line.lower() and 'response' in line.lower():
            current['failure_type'] = 'empty_response'
        elif 'too short' in line.lower() or 'stub' in line.lower():
            current['failure_type'] = 'too_short'
        elif line.startswith('```') and current.get('description') == '':
            pass  # about to read description
        elif current.get('task_name') and not line.startswith('```') and not line.startswith('#'):
            if len(current['description']) < 500:
                current['description'] += line.strip() + ' '

    if current.get('task_name'):
        failures.append(current)

    return failures


def compress_failing_directive(task_name: str, bloated_description: str) -> str:
    """
    Use local Qwen to compress a bloated directive description to <2000 chars.
    Falls back to simple truncation with key-field extraction if Ollama unavailable.

    This is the Source 5 compression handler:
      bloated prompt -> Qwen preprocessor -> compact high-calorie spec -> MiniMax builds it
    """
    # Fast path: if description already fits, no compression needed
    if len(bloated_description) <= COMPRESSION_THRESHOLD:
        return bloated_description

    log.info('[architect] Compressing %s (%d chars -> target <2000)', task_name, len(bloated_description))

    system_prompt = (
        'You are a technical spec compressor for an AI code generation system. '
        'Rewrite the following directive description to be under 2000 characters. '
        'STRIP: boilerplate, philosophy, FRD context, repeated explanations. '
        'KEEP ONLY: '
        '1. Exact API routes and HTTP methods. '
        '2. Exact JSON request/response field names. '
        '3. Specific ws_query/ws_write table names and key fields. '
        '4. Idempotency rules and security constraints (TTL, token expiry, used=FALSE checks). '
        '5. Port number, run() and heartbeat requirement. '
        'Output only the compressed description text, no preamble.'
    )

    try:
        r = requests.post(
            OLLAMA_URL + '/api/generate',
            json={
                'model': 'qwen2.5-coder:32b',
                'system': system_prompt,
                'prompt': f'Compress this directive description:\n{bloated_description[:8000]}',
                'stream': False,
                'options': {'temperature': 0.1, 'num_predict': 600}
            },
            timeout=120
        )
        if r.status_code == 200:
            compressed = r.json().get('response', '').strip()
            if compressed and len(compressed) < COMPRESSION_THRESHOLD:
                log.info('[architect] Compressed to %d chars via Qwen', len(compressed))
                return compressed
            log.warning('[architect] Qwen compression produced %d chars -- using fallback', len(compressed))
    except Exception as e:
        log.warning('[architect] Ollama compression failed: %s -- using fallback', e)

    # Fallback: extract key fields manually via regex
    lines = bloated_description.split('.')
    # Keep lines containing route patterns, ws_ calls, port numbers, table names
    key_patterns = re.compile(
        r'(POST|GET|PUT|DELETE|/api/|ws_write|ws_query|port\s*\d+|'
        r'\d{4}|uuid4|expires_at|used=|heartbeat|run\(\)|uvicorn|smoke test)',
        re.IGNORECASE
    )
    kept = [l.strip() for l in lines if key_patterns.search(l) and len(l.strip()) > 10]
    fallback = '. '.join(kept[:20])[:1900]
    log.info('[architect] Fallback compression: %d chars', len(fallback))
    return fallback


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def read_research_novelty():
    if not RESEARCH_KB.exists():
        return []
    content = RESEARCH_KB.read_text()
    novelty = []
    in_security_section = False
    for line in content.split('\n'):
        if '## 6' in line or ('Security' in line and '##' in line):
            in_security_section = True
        elif line.startswith('## ') and in_security_section:
            in_security_section = False
        if in_security_section and line.startswith('- **'):
            m = re.match(r'- \*\*([^*]+)\*\*: (.+)', line)
            if m:
                novelty.append({'concept': m.group(1), 'description': m.group(2)[:200]})
    return novelty


def read_live_threats(limit=20):
    try:
        r = requests.post(WRITE_SERVICE + '/query',
            json={'sql': (
                "SELECT title, topics FROM world_articles "
                "WHERE topics ILIKE '%cybersecurity%' OR topics ILIKE '%ai%' "
                "ORDER BY processed_at DESC LIMIT " + str(limit)
            )}, timeout=8)
        if r.status_code == 200:
            return r.json().get('rows', [])
    except Exception:
        pass
    return []


def read_paused_files():
    if PAUSED_JSON.exists():
        try:
            return set(json.loads(PAUSED_JSON.read_text()).get('paused', []))
        except Exception:
            pass
    return set()


def get_built_files():
    built = set()
    for f in PROJECT_DIR.glob('*.py'):
        built.add(f.name)
    for f in PROJECT_DIR.glob('*.html'):
        built.add(f.name)
    return built


def get_queued_tasks():
    tasks = set()
    for fp in glob.glob(str(DIRECTIVE_DIR / '*.json')):
        try:
            tasks.add(json.loads(Path(fp).read_text()).get('task', ''))
        except Exception:
            pass
    return tasks


def next_seq():
    nums = []
    for fp in glob.glob(str(DIRECTIVE_DIR / '*.json')):
        try: nums.append(int(Path(fp).name.split('_')[0]))
        except ValueError: pass
    return max(nums, default=120) + 1


def write_directive(task, description, phase, complexity, priority, pillar, source_note=''):
    DIRECTIVE_DIR.mkdir(exist_ok=True)
    seq = next_seq()
    fname = str(seq).zfill(3) + '_' + task + '.json'
    fpath = DIRECTIVE_DIR / fname
    output_file = task.replace('build_', '')
    if not output_file.endswith('.html'):
        output_file += '.py'
    payload = {
        'task': task, 'handler': 'generate_file',
        'output_file': output_file, 'complexity': complexity,
        'phase': phase, 'priority': float(priority),
        'description': description,
        'from': 'enterprise_architect_loop',
        'pillar': pillar, 'source_note': source_note
    }
    fpath.write_text(json.dumps(payload, indent=2))
    log.info('[architect] Queued: %s (pillar=%s priority=%.2f)', fname, pillar, priority)
    return fname


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate_and_queue(max_directives=3, dry_run=False):
    built   = get_built_files()
    queued  = get_queued_tasks()
    paused  = read_paused_files()
    novelty = read_research_novelty()
    threats = read_live_threats(10)
    failures = read_generation_failures()  # Source 5

    log.info('[architect] Built: %d  Queued: %d  Paused: %d  Failures: %d',
             len(built), len(queued), len(paused), len(failures))

    queued_count = 0
    directives_written = []

    # ── PASS 0: Source 5 -- Compress and re-queue failed builds ──────────────
    # Runs FIRST so compression fixes unblock the registry queue in PASS 1.
    # Only triggers for 'empty_response' failures (context window overflow).
    seen_task_compressions = set()
    for failure in failures:
        if queued_count >= max_directives:
            break
        task_name = failure.get('task_name', '')
        failure_type = failure.get('failure_type', '')

        if failure_type != 'empty_response':
            continue
        if not task_name:
            continue

        # Find the original directive for this task
        original_description = failure.get('description', '')
        compact_task = task_name + '_compact'

        # Skip if compact variant already queued or built
        output_file = task_name.replace('build_', '') + '.py'
        if output_file in built:
            log.debug('[architect] %s already built -- skipping compression', task_name)
            continue
        if compact_task in queued or task_name in seen_task_compressions:
            log.debug('[architect] %s compact already queued', task_name)
            continue

        # Find the maturity registry spec for the full description
        registry_key = output_file
        spec = MATURITY_REGISTRY.get(registry_key)
        full_description = spec['description'] if spec else original_description

        if len(full_description) <= COMPRESSION_THRESHOLD:
            # Description isn't bloated -- failure has another cause
            log.debug('[architect] %s description %d chars -- not a context overflow', task_name, len(full_description))
            continue

        seen_task_compressions.add(task_name)
        compressed = compress_failing_directive(task_name, full_description)

        if not dry_run:
            fname = write_directive(
                task=compact_task,
                description=compressed,
                phase=spec['phase'] if spec else '21',
                complexity=spec['complexity'] if spec else 'high',
                priority=(spec['priority'] if spec else 0.90) + 0.001,  # slightly higher than original
                pillar=spec['pillar'] if spec else 'FOUNDATION',
                source_note=f'source5_compression:{task_name}:{failure_type}'
            )
            directives_written.append(fname)
            log.info('[architect] PASS 0: Compression re-queue -- %s -> %s', task_name, fname)
        else:
            log.info('[architect] DRY RUN PASS 0: Would compress+requeue %s (%d->~%d chars)',
                     task_name, len(full_description), min(len(full_description)//3, 1900))
        queued_count += 1

    # ── PASS 1: Maturity registry gaps (deterministic) ────────────────────────
    registry_items = sorted(
        [(f, d) for f, d in MATURITY_REGISTRY.items() if not d.get('skip')],
        key=lambda x: x[1].get('priority', 0.5), reverse=True
    )

    for output_file, spec in registry_items:
        if queued_count >= max_directives:
            break
        task_name = 'build_' + output_file.replace('.py', '').replace('.html', '')
        compact_task = task_name + '_compact'
        if output_file in built:
            continue
        if task_name in queued or compact_task in queued:
            log.debug('[architect] QUEUED: %s', task_name)
            continue
        if output_file in paused:
            log.warning('[architect] PAUSED: %s -- skipping', output_file)
            continue
        deps = spec.get('depends_on', [])
        unmet = [d for d in deps if d not in built]
        if unmet:
            log.info('[architect] Deferred %s -- waiting for: %s', output_file, unmet)
            continue

        description = spec['description']
        # Auto-compress if description is bloated
        if len(description) > COMPRESSION_THRESHOLD:
            description = compress_failing_directive(task_name, description)

        if not dry_run:
            fname = write_directive(
                task=task_name, description=description,
                phase=spec['phase'], complexity=spec['complexity'],
                priority=spec['priority'], pillar=spec['pillar'],
                source_note='maturity_registry'
            )
            directives_written.append(fname)
        else:
            log.info('[architect] DRY RUN PASS 1: would queue %s (pillar=%s, %d chars)',
                     task_name, spec['pillar'], len(description))
        queued_count += 1

    # ── PASS 2: Research novelty ──────────────────────────────────────────────
    if queued_count < max_directives and novelty:
        RESEARCH_DIRECTIVE_MAP = {
            'Stateful Trust Inference': {
                'task': 'build_stateful_trust_inference',
                'description': (
                    'Stateful trust inference daemon. For each MCP in registry: '
                    'ws_query mcp_signal_scores WHERE server_id=? ORDER BY scored_at ASC. '
                    'Compute Bayesian posterior: prior=0.5, update with each signal score. '
                    'If posterior drops >2 sigma from 30d rolling mean: '
                    'ws_write mesh_events {event_type=trust_anomaly, severity=HIGH}. '
                    'ws_write mcp_server_registry composite_trust=posterior. '
                    'Poll 3600s. Port 8793. GET /health. Heartbeat. No duckdb.'
                ),
                'phase': '21', 'complexity': 'high', 'priority': 0.79, 'pillar': 'NLP'
            },
            'In-loop safeguards': {
                'task': 'build_inloop_validation_agent',
                'description': (
                    'In-loop validation checkpoint daemon. '
                    'ws_query mesh_events WHERE event_type=assessment_step AND created_at>now()-60s. '
                    'If trust_score in consecutive events changed >20 in same assessment run: '
                    'ws_write corrections {action=assessment_drift_detected, agent_id=source_agent}. '
                    'Poll 60s. Port 8794. GET /health. Heartbeat. No duckdb.'
                ),
                'phase': '21', 'complexity': 'medium', 'priority': 0.77, 'pillar': 'BRMS'
            },
        }
        for item in novelty:
            if queued_count >= max_directives: break
            spec = RESEARCH_DIRECTIVE_MAP.get(item['concept'])
            if spec and spec['task'] not in queued:
                if not dry_run:
                    fname = write_directive(source_note=f'research_kb:{item["concept"]}', **spec)
                    directives_written.append(fname)
                else:
                    log.info('[architect] DRY RUN PASS 2: %s', spec['task'])
                queued_count += 1

    # ── PASS 3: Live threat intel ─────────────────────────────────────────────
    if queued_count < max_directives and threats:
        for article in threats:
            if queued_count >= max_directives: break
            title = article.get('title', '').lower()
            if 'social engineering' in title and 'build_social_engineering_signal_detector' not in queued:
                task = 'build_social_engineering_signal_detector'
                if not dry_run:
                    fname = write_directive(
                        task=task,
                        description=(
                            'Social engineering signal detector. '
                            'ws_query mcp_server_registry all descriptions and metadata. '
                            'Scan for patterns: urgency ("must use", "required now"), '
                            'authority impersonation ("from Anthropic", "official MCP"), '
                            'FOMO ("your data at risk", "limited access"). '
                            'On match: ws_write mcp_threat_associations {threat_type=social_engineering, severity=MEDIUM}. '
                            'Pattern list in SOCIAL_ENG_PATTERNS constant. Poll 14400s. '
                            'Port 8795. GET /health. Heartbeat. No duckdb.'
                        ),
                        phase='21', complexity='medium', priority=0.76,
                        pillar='THREAT_INTEL',
                        source_note=f'world_articles:{article["title"][:60]}'
                    )
                    directives_written.append(fname)
                else:
                    log.info('[architect] DRY RUN PASS 3: social_engineering_detector from "%s"',
                             article['title'][:60])
                queued_count += 1
                break

    summary = {
        'directives_written': len(directives_written),
        'files': directives_written,
        'built_count': len(built),
        'failures_processed': len([f for f in failures if f['failure_type'] == 'empty_response']),
        'sources_checked': 5
    }
    log.info('[architect] Complete: %d directive(s) written', len(directives_written))
    return summary


if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--max', type=int, default=3)
    args = parser.parse_args()
    result = evaluate_and_queue(max_directives=args.max, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))