#!/usr/bin/env python3
"""
auto_promoter.py -- continuous candidate->registry promotion daemon.
Runs every 10 minutes. Promotes all eligible candidates, marks promoted
via subquery (avoids 400 on UPDATE..IN). Logs registry count each cycle.
Target: keep registry growing until 20k, then switch to signal-depth mode.
"""
import json, time, logging, urllib.request
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [auto_promoter] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/auto_promoter.log'),
              logging.StreamHandler()]
)
log = logging.getLogger(__name__)
WS = 'http://127.0.0.1:8772'
TARGET = 20000
CYCLE  = 600  # 10 min

def q(sql):
    data = json.dumps({'sql': sql}).encode()
    req  = urllib.request.Request(f'{WS}/query', data=data, method='POST',
               headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get('rows', [])

def promote_batch(limit=500):
    now = datetime.now(timezone.utc).isoformat()
    r = q(f"""
        INSERT INTO mcp_server_registry
            (server_id, name, url, description, registry_source,
             verdict, trust_score, first_seen, last_seen)
        SELECT gen_random_uuid()::varchar,
               candidate_name, candidate_url,
               COALESCE(candidate_description,''),
               discovered_in_directory,
               'unknown', NULL, first_seen, last_seen
        FROM mcp_discovery_candidates
        WHERE promoted=false AND discovered_status='active'
          AND candidate_url  IS NOT NULL AND candidate_url  != ''
          AND candidate_name IS NOT NULL AND candidate_name != ''
          AND NOT EXISTS (
              SELECT 1 FROM mcp_server_registry r
              WHERE r.url = mcp_discovery_candidates.candidate_url
          )
        LIMIT {limit}
    """)
    added = r[0].get('Count', 0) if r else 0
    # Mark promoted via subquery join (avoids 400)
    if added > 0:
        try:
            q(f"""
                UPDATE mcp_discovery_candidates
                SET promoted=true, reviewed_at='{now}'
                WHERE discovered_status='active'
                  AND candidate_url IN (SELECT url FROM mcp_server_registry)
            """)
        except Exception as e:
            log.debug('Mark promoted non-fatal: %s', e)
    return added

def run_cycle():
    total = q('SELECT COUNT(*) as n FROM mcp_server_registry')[0]['n']
    unpromoted = q("SELECT COUNT(*) as n FROM mcp_discovery_candidates WHERE promoted=false AND discovered_status='active'")[0]['n']

    if total >= TARGET:
        log.info('TARGET REACHED: %d >= %d. Switching to signal-depth mode.', total, TARGET)
        return 0

    if unpromoted == 0:
        log.info('No unpromoted candidates. Registry=%d. Waiting for ingestor.', total)
        return 0

    promoted = 0
    passes = 0
    while True:
        added = promote_batch(500)
        promoted += added
        passes += 1
        if added == 0 or passes > 50:
            break

    new_total = q('SELECT COUNT(*) as n FROM mcp_server_registry')[0]['n']
    log.info('Cycle done: promoted=%d passes=%d registry=%d->%d target=%d gap=%d',
             promoted, passes, total, new_total, TARGET, TARGET - new_total)
    return promoted

if __name__ == '__main__':
    log.info('auto_promoter starting. Target=%d', TARGET)
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(CYCLE)