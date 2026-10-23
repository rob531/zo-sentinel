#!/usr/bin/env python3
"""Quick SQLite mesh_memory.db inspector."""
import sqlite3
from pathlib import Path

DB = Path("/home/workspace/Datasets/zo-mesh/mesh_memory.db")
conn = sqlite3.connect(str(DB))

# Tables
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])

# Columns in memories table
cols = conn.execute("PRAGMA table_info(memories)").fetchall()
print("Columns:", [c[1] for c in cols])

# Memory type distribution
print("\nMemory type counts:")
rows = conn.execute("SELECT memory_type, COUNT(*) as cnt FROM memories GROUP BY memory_type ORDER BY cnt DESC").fetchall()
for r in rows:
    print(f"  {r[0]:<25} {r[1]}")

# Wisdom entries specifically
wisdom = conn.execute("SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM memories WHERE memory_type='wisdom'").fetchone()
print(f"\nWisdom entries: {wisdom[0]}  oldest={wisdom[1]}  newest={wisdom[2]}")

# Total
total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
print(f"Total memories: {total}")

conn.close()