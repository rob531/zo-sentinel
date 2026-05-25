#!/usr/bin/env bash
# Run bootstrap via nohup so it doesn't block
nohup python3 /home/workspace/zo_sentinel/mcp_bootstrap.py > /home/workspace/logs/mcp_bootstrap.log 2>&1 &
echo "Bootstrap PID: $!"