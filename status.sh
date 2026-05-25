#!/bin/bash

# Daemon names
DAEMONS=("mcp_scanner" "signal_analyser" "trust_synthesiser" "threat_intel_ingestor" "risk_ranker" "attestation_engine" "approval_workflow" "registry_api" "search_api")

# Ports for API services
declare -A PORTS
PORTS["approval_workflow"]="8780"
PORTS["registry_api"]="8781"
PORTS["search_api"]="8782"

# Log file paths
declare -A LOGS
LOGS["mcp_scanner"]="/tmp/mcp_scanner.log"
LOGS["signal_analyser"]="/tmp/signal_analyser.log"
LOGS["trust_synthesiser"]="/tmp/trust_synthesiser.log"
LOGS["threat_intel_ingestor"]="/tmp/threat_intel_ingestor.log"
LOGS["risk_ranker"]="/tmp/risk_ranker.log"
LOGS["attestation_engine"]="/tmp/attestation_engine.log"
LOGS["approval_workflow"]="/tmp/approval_workflow.log"
LOGS["registry_api"]="/tmp/registry_api.log"
LOGS["search_api"]="/tmp/search_api.log"

echo "=== Daemon Status ==="
running_count=0
total_count=${#DAEMONS[@]}

for daemon in "${DAEMONS[@]}"; do
    pid=$(pgrep -f "${daemon}.py" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "[OK] $daemon: PID $pid"
        ((running_count++))
    else
        echo "[--] $daemon: not running"
    fi
done

echo ""
echo "=== API Health Checks ==="
for daemon in "${DAEMONS[@]}"; do
    port="${PORTS[$daemon]}"
    if [ -n "$port" ]; then
        response=$(curl -s "http://localhost:$port/health" 2>/dev/null)
        if [ -n "$response" ]; then
            echo "[OK] $daemon (port $port): $response"
        else
            echo "[--] $daemon (port $port): no response"
        fi
    fi
done

echo ""
echo "=== Summary ==="
echo "$running_count/$total_count services running"

echo ""
echo "=== Log Files ==="
for daemon in "${DAEMONS[@]}"; do
    log="${LOGS[$daemon]}"
    if [ -n "$log" ]; then
        if [ -f "$log" ]; then
            echo "--- $log (last 3 lines) ---"
            tail -n 3 "$log" 2>/dev/null || echo "(unable to read)"
        else
            echo "--- $log: not found ---"
        fi
    fi
done