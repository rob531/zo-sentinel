#!/usr/bin/env python3
"""
wisdom_feed.py -- Inject rich DuckDB memories into wisdom synthesis context.

The wisdom synthesiser reads advisories/patterns/insights from SQLite.
This script pulls the richest recent memories from DuckDB and writes
them into mesh_events so they appear in the wisdom input window.

Run once before wisdom_test_loop.py to give it better data.
"""
import sys, json, requests
from datetime import datetime, timezone

WRITE_SERVICE = "http://127.0.0.1:8772"

def ws_query(sql):
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        print(f"  ws_query error: {e}")
    return []

def ws_write(table, row):
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
                          json={"table": table, "rows": row, "wait": True}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

now = datetime.now(timezone.utc).isoformat()

print("Feeding wisdom synthesiser with rich DuckDB context...")

# Pull top behavioral patterns
patterns = ws_query("""
    SELECT content FROM mesh_memory
    WHERE memory_type='behavioral_pattern'
    AND importance > 0.7
    ORDER BY created_at DESC LIMIT 10
""")
print(f"  Found {len(patterns)} high-importance behavioral patterns")

# Pull top learning examples  
learning = ws_query("""
    SELECT content FROM mesh_memory
    WHERE memory_type='learning_example'
    AND importance > 0.7
    ORDER BY created_at DESC LIMIT 10
""")
print(f"  Found {len(learning)} learning examples")

# Pull topic insights
insights = ws_query("""
    SELECT content FROM mesh_memory
    WHERE memory_type='topic_insight'
    ORDER BY created_at DESC LIMIT 10
""")
print(f"  Found {len(insights)} topic insights")

# Pull recent build artifacts (what was built)
builds = ws_query("""
    SELECT content FROM mesh_memory
    WHERE memory_type='build_artifact'
    ORDER BY created_at DESC LIMIT 10
""")
print(f"  Found {len(builds)} build artifacts")

# Write summary advisory to mesh_events for wisdom to consume
all_content = []
for row in patterns[:5]:
    try:
        c = json.loads(row["content"])
        all_content.append(f"Pattern: {str(c)[:100]}")
    except Exception:
        pass

for row in learning[:5]:
    try:
        c = json.loads(row["content"])
        all_content.append(f"Learning: {str(c)[:100]}")
    except Exception:
        pass

for row in builds[:5]:
    try:
        c = json.loads(row["content"])
        task = c.get("task", "?")
        phase = c.get("phase", "?")
        all_content.append(f"Built: {task} (phase {phase})")
    except Exception:
        pass

# Write as a mesh_event advisory
if all_content:
    ok = ws_write("mesh_events", {
        "agent_id":   "wisdom_feed",
        "event_type": "wisdom_context_inject",
        "tier":       "T4",
        "payload":    json.dumps({
            "patterns_count": len(patterns),
            "learning_count": len(learning),
            "insights_count": len(insights),
            "builds_count":   len(builds),
            "sample": all_content[:5],
            "injected_at": now
        }),
        "severity":   "INFO",
        "created_at": now
    })
    print(f"  Advisory written to mesh_events: {'OK' if ok else 'FAILED'}")

# Also write rich advisory to mesh_memory for SQLite path
try:
    import sqlite3
    from pathlib import Path
    db_path = Path("/home/workspace/Datasets/zo-mesh/mesh_memory.db")
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        # Write a rich advisory combining all sources
        advisory_text = (
            f"ZOMesh status: {len(patterns)} behavioral patterns, "
            f"{len(learning)} learning examples, "
            f"{len(builds)} modules built. "
            f"Recent builds include: {', '.join([json.loads(b['content']).get('task','?') for b in builds[:3]])}. "
            f"System is in Phase 5.3+ with escalation ladder active across "
            f"MiniMax, Gemini, and Zo subscriber tiers."
        )
        conn.execute(
            "INSERT INTO memories (agent_id, memory_type, content, confidence, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("wisdom_feed", "advisory",
             json.dumps({"advisory": advisory_text, "source": "wisdom_feed.py"}),
             0.9, now, now)
        )
        conn.commit()
        conn.close()
        print(f"  SQLite advisory written: {advisory_text[:100]}...")
except Exception as e:
    print(f"  SQLite write skipped: {e}")

print("\nDone. Now run:")
print("  python3 /home/workspace/zo_sentinel/wisdom_test_loop.py --cycles 5 --mins 3")