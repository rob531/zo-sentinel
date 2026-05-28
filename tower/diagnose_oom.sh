#!/usr/bin/env bash
# diagnose_oom.sh -- capture OOM-killer evidence for the 2026-05-28
# WorldAgent kill at section 14 of go.sh.
#
# Reads dmesg ring buffer + container limits + recent log timestamps and
# writes a report to /home/workspace/logs/diagnose_oom.<ts>.txt that's
# readable via MCP zo_read_log (matches log dir prefix).
#
# Run with: bash /home/workspace/diagnose_oom.sh

set -u
TS=$(date -u +%Y%m%d_%H%M%SZ)
OUT=/home/workspace/logs/diagnose_oom.$TS.txt

exec > >(tee "$OUT") 2>&1

echo "=== diagnose_oom.sh @ $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "output: $OUT"
echo

echo "--- dmesg ring buffer: OOM / kill / Killed ---"
if command -v dmesg >/dev/null 2>&1; then
  sudo dmesg 2>/dev/null | grep -iE 'killed|oom|out of memory|memory cgroup|allocated' | tail -40 || echo "(no matches)"
else
  echo "dmesg not installed"
fi
echo

echo "--- /var/log/syslog tail (if readable) ---"
if [[ -r /var/log/syslog ]]; then
  sudo tail -200 /var/log/syslog 2>/dev/null | grep -iE 'kill|oom' | tail -20 || echo "(no matches)"
else
  echo "/var/log/syslog not readable"
fi
echo

echo "--- container memory limits ---"
if [[ -r /sys/fs/cgroup/memory.max ]]; then
  echo "cgroup v2 memory.max: $(cat /sys/fs/cgroup/memory.max)"
  echo "cgroup v2 memory.current: $(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo '?')"
  echo "cgroup v2 memory.events.local:"
  cat /sys/fs/cgroup/memory.events.local 2>/dev/null | sed 's/^/  /' || echo "  (missing)"
elif [[ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]]; then
  echo "cgroup v1 limit: $(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)"
  echo "cgroup v1 usage: $(cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || echo '?')"
else
  echo "(no readable cgroup memory files)"
fi
echo

echo "--- /proc/meminfo top lines ---"
head -5 /proc/meminfo 2>/dev/null || echo "(unreadable)"
echo

echo "--- top 10 RSS python procs right now ---"
ps -eo pid,rss,cmd --sort=-rss 2>/dev/null | grep -E 'python' | head -10
echo

echo "--- world_agent restart trace from log ---"
grep -nE 'World Awareness Agent System starting|Daemon mode' /home/workspace/logs/world_agent.log 2>/dev/null | tail -10
echo

echo "--- go.sh world_agent (section 14) recent runs ---"
grep -nE 'WorldAgent.*Killed|section 14|line 520' /home/workspace/logs/*.log 2>/dev/null | tail -10
echo

echo "=== done ==="
echo "report: $OUT"
echo
echo "View from this Claude session via MCP:"
echo "  zo_read_log(log_name='diagnose_oom.$TS', lines=200)"
