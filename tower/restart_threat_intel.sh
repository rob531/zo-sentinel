#!/usr/bin/env bash
# restart_threat_intel.sh -- one-shot restart of threat_intel_ingestor
# after the evidence(100) DDL fix.
#
# Run with:  bash /home/workspace/restart_threat_intel.sh
#
# Avoids copy/paste hazards of multi-line nohup commands.

set -u  # not -e: kill failures are expected when daemon isn't running

echo "=== restart_threat_intel.sh ==="

echo "--- verifying file fix ---"
grep -n 'PRIMARY KEY' /home/workspace/zo_sentinel/threat_intel_ingestor.py | head -2
echo "^^ should show: PRIMARY KEY (server_id, threat_type, evidence)  -- no (100)"
echo "   or canonical: UNIQUE (server_id, threat_type, source)"
echo

echo "--- killing existing instance(s) ---"
BEFORE=$(pgrep -cf 'threat_intel_ingestor.py' 2>/dev/null || echo 0)
echo "running before: $BEFORE"
pkill -f 'threat_intel_ingestor.py' 2>/dev/null || true
sleep 2
AFTER_KILL=$(pgrep -cf 'threat_intel_ingestor.py' 2>/dev/null || echo 0)
echo "running after kill: $AFTER_KILL"
echo

echo "--- starting under daemon_wrapper.sh ---"
nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh threat_intel_ingestor /home/workspace/zo_sentinel/threat_intel_ingestor.py >> /home/workspace/logs/threat_intel_ingestor.log 2>&1 &
sleep 3
pgrep -af 'threat_intel_ingestor.py' || echo "WARN: no threat_intel_ingestor process visible after 3s"
echo

echo "--- recent write_service log (should NOT show evidence(100) errors) ---"
tail -10 /home/workspace/logs/write_service.log
echo

echo "=== done ==="
echo "Watch for 30s: tail -f /home/workspace/logs/write_service.log"
echo "If no new 'evidence(100)' parser errors and no 'malloc' aborts appear,"
echo "the fix is working."
