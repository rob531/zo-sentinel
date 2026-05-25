# ZO-SENTINEL Operations Runbook

**Last Updated:** 2026-05-24 23:46 UTC
**Version:** 1.0

---

## 1. System Architecture

### 1.1 Supervisord Configuration

The system uses **dual supervisord** setup:

| Config | Location | Port | Purpose |
|--------|----------|------|---------|
| System-wide | `/etc/zo/supervisor.conf` | 29001 | Core infrastructure |
| User-level | `/etc/zo/supervisord-user.conf` | 29011 | User daemons |

**Supervisord Control:**
```bash
# System-wide (core services)
supervisorctl -c /etc/zo/supervisor.conf status

# User-level (zo-sentinel daemons)
supervisorctl -c /etc/zo/supervisord-user.conf status

# Health check both
curl -s http://127.0.0.1:29011 RPC2
curl -s http://127.0.0.1:29001 RPC2
```

### 1.2 Active Services Inventory

#### Core Infrastructure Services
| Service | PID | Memory | Purpose |
|---------|-----|--------|---------|
| frpc-frp-standard-2/3/4/6 | Various | ~54MB | FRP tunnel clients |
| cyber-intel-proxy | 104 | 47MB | Proxy server on port 8888 |
| sshd | - | - | SSH daemon |

#### Mesh Services
| Service | PID | Memory | Purpose |
|---------|-----|--------|---------|
| write_service | 1847 | 887MB | DuckDB write service on port 8772 |
| inference_router_service | 1889 | 88MB | ML inference routing |
| zo_sentinel_builder | 2411, 3341 | 66MB | Builder daemon |

#### Application Services
| Service | PID | Port | Purpose |
|---------|-----|------|---------|
| intent-engine | 123 | 8771 | Intent rotation service |
| world-agent | 127 | 8766 | World context API |
| portalpha-api | 285 | 8765 | Portfolio alpha API |
| zo-mesh | 295 | 8767 | Mesh guardian |
| zo-mcp-server | 167 | - | MCP server |
| zo-sentinel-ui | 183 | - | UI server |
| build_watcher_api | 153 | - | Build monitoring |
| execution_api_service | 164 | - | Execution API |

### 1.3 Port Mapping

| Port | Service | Endpoint |
|------|---------|----------|
| 8772 | write_service | HTTP API |
| 8771 | intent-engine | Intent rotation |
| 8766 | world-agent | Context API |
| 8765 | portalpha-api | Portfolio API |
| 8767 | zo-mesh | Mesh API |
| 8888 | cyber-intel-proxy | Proxy server |
| 3030 | mcpo | MCP tools |

---

## 2. Service Management

### 2.1 Checking Service Status

```bash
# Check all user-level services
supervisorctl -c /etc/zo/supervisord-user.conf status

# Check all system-level services
supervisorctl -c /etc/zo/supervisor.conf status

# Check if process running (any method)
ps aux | grep <service_name>

# Check specific PID
ps -p <PID>
```

### 2.2 Restarting Services

```bash
# Restart via supervisord (user-level)
supervisorctl -c /etc/zo/supervisord-user.conf restart <service_name>

# Restart via supervisord (system-wide)
supervisorctl -c /etc/zo/supervisor.conf restart <service_name>

# Kill and restart manually
kill -TERM <PID>
# Wait 5 seconds, process should auto-restart via supervisord
```

### 2.3 Viewing Logs

```bash
# User-level service logs (via supervisord)
supervisorctl -c /etc/zo/supervisord-user.conf tail -f <service_name>

# System logs in /dev/shm/
tail -f /dev/shm/<service_name>.log

# Application logs
ls -la /home/workspace/logs/
tail -f /home/workspace/logs/<service>.log
```

---

## 3. Health Checks

### 3.1 write_service Health

```bash
# Primary health check
curl -s http://127.0.0.1:8772/health

# Expected response:
# {"status":"ok","version":"1.3.0","queue_depth":0,"total_written":10950,...}

# If not responding, check process
ps aux | grep write_service
```

### 3.2 intent-engine Health

```bash
# Check process
ps aux | grep intent_rotation_service

# View logs
tail -f /home/workspace/logs/intent_engine.log
```

### 3.3 world-agent Health

```bash
# Check process
ps aux | grep intent_engine_daemon

# Test API
curl -s http://localhost:8766/brief
```

### 3.4 portalpha-api Health

```bash
# Check process
ps aux | grep api_server | grep portalpha

# Test API
curl -s http://localhost:8765/health
```

### 3.5 zo-mesh Health

```bash
# Check process
ps aux | grep mesh_guardian

# Check mesh status
curl -s http://localhost:8767/status
```

### 3.6 System-wide Health Check Script

```bash
#!/bin/bash
echo "=== ZO-SENTINEL Health Check ==="
echo ""

echo "--- write_service ---"
curl -s http://127.0.0.1:8772/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Status: {d[\"status\"]}, Written: {d[\"total_written\"]}, Errors: {d[\"total_errors\"]}')" 2>/dev/null || echo "UNAVAILABLE"

echo ""
echo "--- Supervisord Services ---"
supervisorctl -c /etc/zo/supervisord-user.conf status

echo ""
echo "--- Key Processes ---"
ps aux | grep -E "write_service|inference_router|zo_sentinel_builder" | grep -v grep | awk '{print $11, $6"KB"}'
```

---

## 4. Recovery Runbook

### 4.1 write_service Staleness

**Symptoms:**
- `curl http://127.0.0.1:8772/health` returns error or timeout
- `service_health` table shows stale heartbeat
- `total_errors` increasing without `total_written` incrementing

**Recovery Steps:**

1. **Check if process is running:**
   ```bash
   ps aux | grep write_service | grep -v grep
   ```

2. **If process dead, restart:**
   ```bash
   supervisorctl -c /etc/zo/supervisord-user.conf restart zo_mesh
   # OR
   supervisorctl -c /etc/zo/supervisord-user.conf restart write_service
   ```

3. **Verify recovery:**
   ```bash
   sleep 5
   curl -s http://127.0.0.1:8772/health
   ```

4. **If supervisord restart fails, manual restart:**
   ```bash
   kill -9 $(pgrep -f write_service.py) 2>/dev/null
   cd /home/workspace/zo_mesh
   nohup python3 write_service.py > /dev/null 2>&1 &
   sleep 3
   curl -s http://127.0.0.1:8772/health
   ```

### 4.2 intent-engine Staleness

**Symptoms:**
- No new intents being generated
- Intent rotation not responding

**Recovery Steps:**

1. **Check process:**
   ```bash
   ps aux | grep intent_rotation_service
   ```

2. **Restart service:**
   ```bash
   supervisorctl -c /etc/zo/supervisord-user.conf restart intent-engine
   ```

3. **Verify:**
   ```bash
   curl -s http://127.0.0.1:8771/status 2>/dev/null || echo "Check logs"
   tail -20 /home/workspace/logs/intent_engine.log
   ```

### 4.3 world-agent Staleness

**Symptoms:**
- `/brief` or `/world-state` endpoints timeout
- Stale world context

**Recovery Steps:**

1. **Check process:**
   ```bash
   ps aux | grep intent_engine_daemon
   ```

2. **Restart:**
   ```bash
   kill -TERM $(pgrep -f intent_engine_daemon.py)
   sleep 2
   cd /home/workspace/Skills/childofintent-intent-engine/scripts
   nohup python intent_engine_daemon.py > /home/workspace/logs/intent_engine_daemon.log 2>&1 &
   ```

3. **Verify:**
   ```bash
   curl -s http://localhost:8766/brief | head -c 200
   ```

### 4.4 zo-mesh Staleness

**Symptoms:**
- Mesh directives not being processed
- Agent schedules not firing

**Recovery Steps:**

1. **Check mesh_guardian:**
   ```bash
   ps aux | grep mesh_guardian
   ```

2. **Restart mesh services:**
   ```bash
   supervisorctl -c /etc/zo/supervisord-user.conf restart zo-mesh
   ```

3. **Verify:**
   ```bash
   curl -s http://localhost:8767/status
   ```

### 4.5 DuckDB Lock/Performance Issues

**Symptoms:**
- write_service responding but slow
- "database is locked" errors

**Recovery Steps:**

1. **Check for lock files:**
   ```bash
   ls -la /home/workspace/Datasets/zo-mesh/*.lock 2>/dev/null
   ```

2. **Check active connections:**
   ```bash
   ps aux | grep -E "write_service|inference_router" | wc -l
   ```

3. **If stuck, restart write_service:**
   ```bash
   supervisorctl -c /etc/zo/supervisord-user.conf restart zo_mesh
   ```

---

## 5. Log File Locations

| Service | Log Location |
|---------|-------------|
| intent-engine | `/home/workspace/logs/intent_engine.log` |
| zo_mesh | `/home/workspace/logs/zo_mesh.log` |
| intent_engine_daemon | `/home/workspace/logs/intent_engine_daemon.log` |
| trust_synthesiser | `/home/workspace/logs/trust_synthesiser.log` |
| signal_bridge | `/home/workspace/logs/signal_bridge.log` |
| risk_ranker | `/home/workspace/logs/risk_ranker.log` |
| attestation_engine | `/home/workspace/logs/attestation_engine.log` |
| ui_server | `/home/workspace/logs/ui_server.log` |
| mcp_directory_ingestor | `/home/workspace/logs/mcp_directory_ingestor.log` |
| threat_intel_ingestor | `/home/workspace/logs/threat_intel_ingestor.log` |
| Supervisord system logs | `/dev/shm/supervisord*.log` |

---

## 6. Database Operations

### 6.1 DuckDB (write_service)

```bash
# Access via write_service API
curl -s -X POST http://127.0.0.1:8772/query \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT COUNT(*) FROM service_health"}'

# Query service_health
curl -s -X POST http://127.0.0.1:8772/query \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT service_name, status, last_heartbeat FROM service_health ORDER BY last_heartbeat"}'

# Check directive queue
curl -s -X POST http://127.0.0.1:8772/query \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT COUNT(*) FROM directives WHERE status = '\''pending'\''"}'
```

### 6.2 SQLite (mesh_memory.db)

```bash
# Direct access (read-only recommended)
sqlite3 /home/workspace/Datasets/zo-mesh/mesh_memory.db "SELECT * FROM agent_state LIMIT 5;"

# Check table structure
sqlite3 /home/workspace/Datasets/zo-mesh/mesh_memory.db ".schema agent_state"
```

---

## 7. Common Issues & Resolution

### 7.1 Port Already in Use

```bash
# Find process using port
lsof -i :8772
netstat -tlnp | grep 8772

# Kill specific process
kill -9 <PID>
```

### 7.2 Supervisord Socket Error

**Error:** `Cannot assign requested address` on supervisord port

**Cause:** Port conflict or supervisord not running on that interface

**Resolution:**
```bash
# Check supervisord is running
ps aux | grep supervisord

# Check if socket is bound
netstat -tlnp | grep 29011

# If supervisord down, restart
/usr/local/bin/supervisord -c /etc/zo/supervisord-user.conf
```

### 7.3 High Memory Usage

```bash
# Find memory-hungry processes
ps aux --sort=-%mem | head -10

# Check write_service memory (normal ~800MB)
ps aux | grep write_service | awk '{print $6/1024 "MB"}'

# Force garbage collection (if applicable)
# Restart service to clear memory
supervisorctl -c /etc/zo/supervisord-user.conf restart <service>
```

### 7.4 Directive Queue Backlog

```bash
# Check queue depth
curl -s http://127.0.0.1:8772/health | python3 -c "import sys,json; print('Queue:', json.load(sys.stdin)['queue_depth'])"

# Check pending directives count
curl -s -X POST http://127.0.0.1:8772/query \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT status, COUNT(*) FROM directives GROUP BY status"}'
```

---

## 8. Escalation Procedures

### 8.1 Automated Recovery

Check these files for automated recovery directives:
- `/home/workspace/zo_sentinel/standing_goals.json`
- `/home/workspace/zo_sentinel/standing_goals_fallback.py`

### 8.2 Manual Intervention

1. **Document issue:** Note time, symptoms, error messages
2. **Attempt recovery steps** from Section 4
3. **If unresolved after 3 attempts:**
   - Check `/home/workspace/zo_sentinel/directives/pending/` for stuck directives
   - Review recent logs for patterns
   - Check disk space: `df -h`
4. **Full restart:** `supervisorctl -c /etc/zo/supervisord-user.conf restart all`

### 8.3 Emergency Contacts

For critical failures after standard recovery steps fail:
1. Check Slack `#ops-alerts` channel
2. Review `/home/workspace/zo_sentinel/reports/` for recent incidents
3. Check `/home/workspace/zo_sentinel/SESSION_CLOSEOUT*.md` for recent session context

---

## 9. Quick Reference Card

```bash
# === HEALTH CHECK ===
curl -s http://127.0.0.1:8772/health | jq .

# === SERVICE STATUS ===
supervisorctl -c /etc/zo/supervisord-user.conf status

# === RESTART SERVICE ===
supervisorctl -c /etc/zo/supervisord-user.conf restart <name>

# === VIEW LOG ===
supervisorctl -c /etc/zo/supervisord-user.conf tail -f <name>

# === CHECK PROCESS ===
ps aux | grep <name> | grep -v grep

# === FULL RESTART ===
supervisorctl -c /etc/zo/supervisord-user.conf restart all
```

---

**Document Owner:** ZO-SENTINEL Operations
**Review Frequency:** Weekly
**Next Review:** 2026-05-31