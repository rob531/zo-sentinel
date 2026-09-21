# Check schema truth and bus catalog
import json
import os

# Read SCHEMA_TRUTH.md
with open('docs/SCHEMA_TRUTH.md', 'r') as f:
    schema_content = f.read()

# Read bus_catalog.json
with open('schema/bus_catalog.json', 'r') as f:
    bus_catalog = json.load(f)

print("SCHEMA_TRUTH.md content:")
print(schema_content[:3000])  # First 3000 chars
print("\n\nBUS CATALOG tables:")
print(bus_catalog)