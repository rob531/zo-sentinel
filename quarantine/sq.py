import sqlite3
from pathlib import Path
DB = Path("/home/workspace/Datasets/zo-mesh/mesh_memory.db")
conn = sqlite3.connect(str(DB))
tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('TABLES:', tables)
cols = [c[1] for c in conn.execute("PRAGMA table_info(memories)").fetchall()]
print('COLUMNS:', cols)
rows = conn.execute("SELECT memory_type, COUNT(*) as cnt FROM memories GROUP BY memory_type ORDER BY cnt DESC").fetchall()
print('\nTYPE COUNTS:')
for r in rows: print(f'  {r[0]:<25} {r[1]}')
w = conn.execute("SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM memories WHERE memory_type='wisdom'").fetchone()
print(f'\nWisdom rows: {w[0]}  oldest={w[1]}  newest={w[2]}')
print('Total:', conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0])
conn.close()