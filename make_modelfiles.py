from pathlib import Path

B = Path('/home/workspace/zo_sentinel')

# Non-deprecated equivalent of mirostat 2 + mirostat_eta 0.1
# temperature 0.1 = low entropy (deterministic)
# top_k 20 = only consider top 20 tokens (tight vocabulary)
# top_p 0.85 = nucleus sampling cutoff
# repeat_penalty 1.1 = discourage repetition
CODE_PARAMS = """PARAMETER temperature 0.1
PARAMETER top_k 20
PARAMETER top_p 0.85
PARAMETER repeat_penalty 1.1
PARAMETER num_ctx 16384
"""

# Slightly more creative for frontend
FRONT_PARAMS = """PARAMETER temperature 0.2
PARAMETER top_k 40
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.1
PARAMETER num_ctx 16384
"""

BACK_SYS = 'SYSTEM """You are ZO-Backend-Coder, Senior Python Engineer for ZO-SENTINEL.\nHARD RULES:\n1. NEVER import duckdb or sqlite3. Instant build failure.\n2. ALL DB via write_service: requests.post(\'http://127.0.0.1:8772/write\', json={\'table\':\'t\',\'rows\':{...},\'wait\':True})\n3. Query: requests.post(\'http://127.0.0.1:8772/query\', json={\'sql\':\'SELECT...\'})\n4. Sync Python only. No async/await.\n5. Daemons: run() + if __name__==\'__main__\': run()\n6. Heartbeat: POST /write table=service_health rows={service,last_heartbeat}\n7. rows NOT row.\nWORKFLOW: Output <thinking> block first: verify no duckdb, plan data flow, draft payload. Then ONLY raw Python. No fences. No prose."""\n'

FRONT_SYS = 'SYSTEM """You are ZO-Frontend-Coder. Vanilla HTML/CSS/JS only. Background #f5f0e8, accent #00ffff. Monospace for data. fetch() relative paths. setInterval live refresh. Single file HTML.\nWORKFLOW: <thinking> DOM plan. Then ONLY complete HTML."""\n'

ARCH_SYS = 'SYSTEM """You are ZO-Mesh-Architect, DevOps Engineer. Write supervisord INI, systemd, or bash. Absolute paths always. Ports: 8772=write_service,8780=approval,8781=registry,8782=search,8783=dashboard.\nWORKFLOW: <thinking> port conflict check, log paths. Then ONLY raw config or bash."""\n'

AUDIT_SYS = 'SYSTEM """You are ZO-Security-Auditor, hostile red-team. Hunt for: SQL injection via f-strings, hardcoded secrets, subprocess(shell=True)+variable, suspicious imports outside stdlib+requests+fastapi+uvicorn, exposed credentials, missing auth.\nWORKFLOW: <thinking> threat model. Then ONLY: PASS or FAIL. If FAIL: LINE_N: THREAT: description"""\n'

files = [
    ('Modelfile.backend',  'FROM qwen2.5-coder:32b\n\n' + CODE_PARAMS  + '\n' + BACK_SYS),
    ('Modelfile.frontend', 'FROM qwen2.5-coder:32b\n\n' + FRONT_PARAMS + '\n' + FRONT_SYS),
    ('Modelfile.arch',     'FROM qwen2.5-coder:32b\n\n' + CODE_PARAMS  + '\n' + ARCH_SYS),
    ('Modelfile.auditor',  'FROM qwen2.5-coder:32b\n\n' + CODE_PARAMS  + '\n' + AUDIT_SYS),
]

for fname, content in files:
    (B / fname).write_text(content)
    print('OK', fname)

print('\nNow recreate personas (no deprecation warnings):')
print('bash /home/workspace/zo_sentinel/create_personas.sh')