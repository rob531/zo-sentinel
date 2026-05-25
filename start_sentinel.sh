#!/usr/bin/env bash
# start_sentinel.sh -- start UI + scanner, inject MINIMAX key into supervisord
# Usage: bash /home/workspace/zo_sentinel/start_sentinel.sh
# MINIMAX_API_KEY must already be exported in your shell (echo $MINIMAX_API_KEY to verify)

SUPCTL="supervisorctl -c /etc/zo/supervisord-user.conf"
LOGS=/home/workspace/logs
SENTINEL=/home/workspace/zo_sentinel

# 1. Start UI server on port 8790
pkill -f ui_server.py 2>/dev/null; sleep 1
nohup python3 $SENTINEL/ui_server.py >> $LOGS/zo_sentinel_ui.log 2>&1 &
UI_PID=$!
sleep 2
if kill -0 $UI_PID 2>/dev/null; then
    echo "[OK] UI server PID $UI_PID on port 8790"
else
    echo "[!!] UI server failed to start -- check $LOGS/zo_sentinel_ui.log"
fi

# 2. Start MCP scanner if not running
if ! pgrep -f mcp_scanner.py >/dev/null; then
    nohup python3 $SENTINEL/mcp_scanner.py >> $LOGS/mcp_scanner.log 2>&1 &
    echo "[OK] MCP scanner started PID $!"
else
    echo "[OK] MCP scanner already running"
fi

# 3. Inject MINIMAX_API_KEY into supervisord + restart builder
if [ -z "$MINIMAX_API_KEY" ]; then
    echo "[!!] MINIMAX_API_KEY not in environment -- builder will use Ollama only"
    echo "     Run: export MINIMAX_API_KEY=\$(echo \$MINIMAX_API_KEY) first"
else
    echo "[OK] MINIMAX_API_KEY found (${#MINIMAX_API_KEY} chars)"

    # Write to /etc/zo/env so go.sh step 19 picks it up on future reboots
    if [ -f /etc/zo/env ]; then
        # Remove old entry if exists, add new one
        grep -v '^MINIMAX_API_KEY=' /etc/zo/env > /tmp/zo_env_tmp 2>/dev/null || true
        echo "MINIMAX_API_KEY=$MINIMAX_API_KEY" >> /tmp/zo_env_tmp
        cp /tmp/zo_env_tmp /etc/zo/env
        echo "[OK] Key written to /etc/zo/env (persists across reboots)"
    fi

    # Pass key to supervisord environment for builder process
    # supervisord inherits the shell env when started, but we can also
    # update the running environment via a wrapper approach
    CONF=/etc/zo/supervisord-user.conf
    if grep -q 'zo_sentinel_builder' $CONF 2>/dev/null; then
        if grep -q 'MINIMAX_API_KEY' $CONF; then
            # Update existing key value
            sed -i "s|MINIMAX_API_KEY=\"[^\"]*\"|MINIMAX_API_KEY=\"$MINIMAX_API_KEY\"|g" $CONF
            echo "[OK] Updated MINIMAX_API_KEY in supervisord config"
        else
            # Add to environment line if it exists, or add new environment line
            if grep -A5 'zo_sentinel_builder' $CONF | grep -q 'environment='; then
                sed -i "/\[program:zo_sentinel_builder\]/,/^\[/ s|environment=|environment=MINIMAX_API_KEY=\"$MINIMAX_API_KEY\",|" $CONF
            fi
            echo "[OK] Added MINIMAX_API_KEY to supervisord config"
        fi
        $SUPCTL reread 2>/dev/null && $SUPCTL update 2>/dev/null || true
    fi

    # Restart builder via supervisord so it inherits the key
    $SUPCTL restart zo_sentinel_builder 2>/dev/null \
        && echo "[OK] Builder restarted with MINIMAX_API_KEY" \
        || {
            # Fallback: kill and restart directly with key in env
            pkill -f zo_sentinel_builder.py 2>/dev/null; sleep 2
            MINIMAX_API_KEY=$MINIMAX_API_KEY nohup python3 /home/workspace/zo_mesh/zo_sentinel_builder.py \
                >> $LOGS/zo_sentinel_builder.log 2>&1 &
            echo "[OK] Builder restarted directly PID $! with MINIMAX_API_KEY"
        }
fi

echo ""
echo "Status:"
echo "  UI:      http://localhost:8790  ($(pgrep -f ui_server.py | wc -l) instance)"
echo "  Builder: $($SUPCTL status zo_sentinel_builder 2>/dev/null | awk '{print $2}') via supervisord"
echo "  Scanner: $(pgrep -f mcp_scanner.py | wc -l) instance"