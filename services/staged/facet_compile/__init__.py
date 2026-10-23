import os

# Read quarantined version
quarantine_path = '/home/workspace/zo_sentinel/quarantine/services/staged/facet_compile/__init__.py'
with open(quarantine_path, 'r') as f:
    quarantined_content = f.read()

print("=== QUARANTINED VERSION ===")
print(quarantined_content)
print("\n=== REAL TABLES SCHEMA ===")
# Check real tables
import requests
resp = requests.post('http://127.0.0.1:8772/query', json={'sql': "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"}, timeout=5)
print(resp.json())