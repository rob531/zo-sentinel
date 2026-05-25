#!/usr/bin/env bash
# fix_sentinel_builder.sh
# Fixes two issues:
#   1. Registers zo_sentinel_builder in supervisord (fixes 'no such process')
#   2. Kills old v1.1.0 process and starts v1.2.0
#   3. Runs requeue to clear poisoned 285-byte files
#
# Run: bash /home/workspace/zo_sentinel/fix_sentinel_builder.sh

set -e
GRN='\033[0;32m'; YLW='\033[0;33m'; BOLD='\033[1m'; NC='\033[0m'
ok(){ echo -e "  ${GRN}OK${NC} $1"; }
warn(){ echo -e "  ${YLW}!!${NC} $1"; }
hdr(){ echo -e "\n${BOLD}=== $1 ===${NC}"; }

hdr "1. Kill old builder (v1.1.0)"
if pgrep -f 'zo_sentinel_builder.py' > /dev/null 2>&1; then
    pkill -f 'zo_sentinel_builder.py' && ok "Killed old builder" || warn "Kill failed"
    sleep 2
else
    ok "Builder not running"
fi

hdr "2. Register in supervisord"
SUPERVISORD_CONF=/etc/zo/supervisord-user.conf
if grep -q 'zo_sentinel_builder' "$SUPERVISORD_CONF" 2>/dev/null; then
    ok "Already registered in supervisord"
else
    cat >> "$SUPERVISORD_CONF" << 'CONF'

[program:zo_sentinel_builder]
command=python3 /home/workspace/zo_mesh/zo_sentinel_builder.py
directory=/home/workspace/zo_mesh
autostart=true
autorestart=true
startretries=5
stdout_logfile=/home/workspace/logs/zo_sentinel_builder.log
stderr_logfile=/home/workspace/logs/zo_sentinel_builder.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
CONF
    ok "Registered in supervisord"
fi

hdr "3. Reload supervisord config"
supervisorctl -c /etc/zo/supervisord-user.conf reread 2>/dev/null && ok "Reread done" || warn "Reread failed"
supervisorctl -c /etc/zo/supervisord-user.conf update 2>/dev/null && ok "Update done" || warn "Update failed"

hdr "4. Clear poisoned 285-byte files"
SENTINEL=/home/workspace/zo_sentinel
for f in schema.py mcp_scanner.py signal_analyser.py trust_synthesiser.py \
          approval_workflow.py schema_v2.py known_threats.py policy_engine.py \
          rug_pull_monitor.py registry_api.py tests/smoke_test.py; do
    fpath="$SENTINEL/$f"
    if [ -f "$fpath" ]; then
        size=$(wc -c < "$fpath")
        if [ "$size" -lt 400 ]; then
            rm "$fpath"
            warn "Removed poisoned: $f ($size bytes)"
        else
            ok "Valid: $f ($size bytes)"
        fi
    fi
done

hdr "5. Restore .done directives for rebuild"
for done_f in "$SENTINEL/directives/"*.done.json; do
    [ -f "$done_f" ] || continue
    original="${done_f/.done.json/.json}"
    # Only restore if the output file was removed (poisoned)
    task=$(python3 -c "import json; d=json.load(open('$done_f')); print(d.get('output_file',''))" 2>/dev/null || echo "")
    if [ -n "$task" ] && [ ! -f "$SENTINEL/$task" ]; then
        mv "$done_f" "$original"
        ok "Restored directive: $(basename $original)"
    else
        ok "Keeping done: $(basename $done_f) (output exists)"
    fi
done

hdr "6. Clear failed idempotency entries"
python3 - << 'PYEOF'
import json
path = '/home/workspace/zo_sentinel/.build_registry.json'
try:
    reg = json.loads(open(path).read())
    ok_entries  = {k: v for k, v in reg.items() if v.get('status') == 'ok'}
    bad_entries = {k: v for k, v in reg.items() if v.get('status') != 'ok'}
    open(path, 'w').write(json.dumps(ok_entries, indent=2))
    print(f'  Registry: kept {len(ok_entries)} ok, cleared {len(bad_entries)} failed')
except Exception as e:
    print(f'  Registry not found or empty: {e}')
PYEOF

hdr "7. Start builder v1.2.0 via supervisord"
supervisorctl -c /etc/zo/supervisord-user.conf start zo_sentinel_builder 2>/dev/null \
    && ok "Builder started via supervisord" \
    || warn "supervisord start failed - trying direct"

# Verify
sleep 2
if pgrep -f 'zo_sentinel_builder.py' > /dev/null 2>&1; then
    PID=$(pgrep -f 'zo_sentinel_builder.py' | head -1)
    ok "Builder running PID $PID"
else
    warn "Builder not running - starting directly"
    nohup python3 /home/workspace/zo_mesh/zo_sentinel_builder.py \
        >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &
    sleep 2
    PID=$(pgrep -f 'zo_sentinel_builder.py' | head -1)
    ok "Builder started PID $PID"
fi

hdr "DONE"
echo ""
echo "  Builder v1.2.0 running with:"
echo "  - Error detection (rejects 285-byte credit errors)"
echo "  - Idempotency (skips already-built files)"
echo "  - Smoke tests (syntax + content validation)"
echo "  - Phase checkpoints"
echo "  - Knowledge base injection"
echo ""
echo "  Monitor: tail -f /home/workspace/logs/zo_sentinel_builder.log"
echo "  Status:  supervisorctl -c /etc/zo/supervisord-user.conf status zo_sentinel_builder"
echo "  Restart: supervisorctl -c /etc/zo/supervisord-user.conf restart zo_sentinel_builder"
echo ""