# ZO-SENTINEL — MCP Trust Intelligence
## Builder Agent Architecture

### The Async Build Pipeline

```
Robin prompts Claude (this conversation)
         ↓
Claude writes directive to mesh_memory
  agent_id = 'zo_sentinel.directive'
  memory_type = 'build_directive'
         ↓ (also written to /directives/*.json as fallback)
zo_sentinel_builder.py polls every 5 min
         ↓
Builder generates code via inference stack:
  Ollama local (free) → InferenceRouter → ZoComputer credits
         ↓
Code written to /home/workspace/zo_sentinel/
         ↓
Completion written back to mesh_events + mesh_memory
         ↓
Claude reads status on next session via MCP tools
```

### Directive Format
```json
{
  "task":        "unique_task_id",
  "handler":     "generate_file | write_raw | run_script",
  "output_file": "relative/path/to/output.py",
  "description": "What to build in detail",
  "complexity":  "low | medium | high",
  "priority":    0.0-1.0,
  "context":     "Additional architecture context",
  "from":        "claude_directive | cli_inject | robin_prompt"
}
```

### Injecting a Directive (CLI)
```bash
# From description
python3 /home/workspace/zo_sentinel/inject_directive.py \
  --task phase4_registry_api \
  --description "Build FastAPI REST endpoint for live registry queries" \
  --file registry_api.py \
  --complexity high

# From JSON file
python3 /home/workspace/zo_sentinel/inject_directive.py \
  --json /home/workspace/zo_sentinel/directives/my_task.json
```

### Project Structure
```
/home/workspace/zo_sentinel/
├── __init__.py
├── schema.py              # DuckDB table creation
├── mcp_scanner.py         # T1: crawls npm/GitHub/Smithery
├── signal_analyser.py     # T2: scores 6 signal dimensions
├── trust_synthesiser.py   # T3: composite verdict generation
├── registry_api.py        # Phase 4: FastAPI REST endpoint
├── inject_directive.py    # CLI directive injector
└── directives/            # Pending build tasks
    ├── 000_sentinel_schema.json
    ├── 001_phase2_mcp_scanner.json
    └── 002_phase3_signal_analyser.json
```

### DuckDB Tables
- `mcp_server_registry`    — master record per discovered server
- `mcp_signal_scores`      — per-signal per-server scores (time-series)
- `mcp_definition_history` — snapshots for rug-pull detection
- `mcp_threat_associations`— links to CVEs, campaigns, threat actors

### Verdict Taxonomy
| Score | Verdict | Meaning |
|-------|---------|--------|
| >75   | TRUSTED_GENERAL | Safe for most production contexts |
| 60-75 | TRUSTED_RESEARCH | Safe for R&D, no sensitive data |
| 45-60 | ENTERPRISE_CONTROLLED | Safe with formal security controls |
| 30-45 | CAUTION_LIMITED | Scoped use only, human oversight |
| 15-30 | HIGH_RISK_ISOLATED | Test environments only |
| <15   | KNOWN_THREAT | Do not deploy |
| null  | INSUFFICIENT | Cannot assess — treat as untrusted |

### Phases
- Phase 1: UI Lookup — COMPLETE (React artifact)
- Phase 2: MCP Scanner T1 — IN BUILD
- Phase 3: Signal Analyser T2 + Trust Synthesiser T3 — IN BUILD
- Phase 4: Live Registry + REST API — PENDING
- Phase 5: Enterprise licensing + commercial API — PLANNED

### Activating the Builder
```bash
# One-time: add to supervisord
cat /home/workspace/zo_mesh/supervisord_sentinel_builder.conf >> /etc/zo/supervisord-user.conf
supervisorctl -c /etc/zo/supervisord-user.conf reread
supervisorctl -c /etc/zo/supervisord-user.conf update

# Or add to go.sh manually:
nohup python3 /home/workspace/zo_mesh/zo_sentinel_builder.py \
  >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &
```