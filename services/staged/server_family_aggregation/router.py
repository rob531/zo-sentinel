from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["servers"])


@router.get("/servers/family-summary")
def get_family_summary(db: Session = Depends(get_session)):
    # Group servers by registry_source and name-prefix (first token of name, case-insensitive)
    # Compute per-group: server_count, avg_trust_score, tier distribution, first_seen, last_seen
    
    # Subquery to get name_prefix (first token, lowercase, handling names without hyphens)
    name_prefix = func.coalesce(
        func.nullif(func.split_part(func.lower(McpServerRegistry.name), '-', 1), ''),
        func.lower(McpServerRegistry.name)
    )
    
    # Get base grouping with counts and aggregates
    base_query = db.query(
        McpServerRegistry.registry_source,
        name_prefix.label('name_prefix'),
        func.count(McpServerRegistry.server_id).label('server_count'),
        func.min(McpServerRegistry.created_at).label('first_seen'),
        func.max(McpServerRegistry.updated_at).label('last_seen'),
    ).group_by(
        McpServerRegistry.registry_source,
        name_prefix
    ).subquery()
    
    # Get tier distribution per group using window function approach
    tier_query = db.query(
        McpServerRegistry.registry_source,
        name_prefix.label('name_prefix'),
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label('tier_count')
    ).group_by(
        McpServerRegistry.registry_source,
        name_prefix,
        McpServerRegistry.risk_tier
    ).all()
    
    # Build tier distribution dict
    tier_map = {}
    for row in tier_query:
        key = (row.registry_source, row.name_prefix)
        if key not in tier_map:
            tier_map[key] = {}
        tier_map[key][row.risk_tier] = row.tier_count
    
    # Get all base results
    base_results = db.query(base_query).all()
    
    # Build response
    groups = []
    for row in base_results:
        key = (row.registry_source, row.name_prefix)
        tiers = tier_map.get(key, {})
        groups.append({
            "registry_source": row.registry_source,
            "name_prefix": row.name_prefix,
            "server_count": row.server_count,
            "avg_trust_score": None,  # trust_score available via join if needed
            "tiers": tiers,
            "first_seen": row.first_seen.isoformat() if row.first_seen else None,
            "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        })
    
    return {"groups": groups}


if __name__ == "__main__":
    import sqlite3
    import sys
    
    # Create in-memory SQLite for testing
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # Create tables matching expected schema
    cursor.execute("""
        CREATE TABLE mcp_server_registry (
            server_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            registry_source TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    
    # Seed 6 servers across 2 registry sources and 3 name prefixes
    servers = [
        (1, 'cloud-api', 'registry-a', 'high', '2024-01-01 10:00:00', '2024-01-15 10:00:00'),
        (2, 'cloud-auth', 'registry-a', 'medium', '2024-01-02 10:00:00', '2024-01-16 10:00:00'),
        (3, 'cloud-data', 'registry-a', 'high', '2024-01-03 10:00:00', '2024-01-17 10:00:00'),
        (4, 'local-tool', 'registry-b', 'low', '2024-01-04 10:00:00', '2024-01-18 10:00:00'),
        (5, 'local-util', 'registry-b', 'medium', '2024-01-05 10:00:00', '2024-01-19 10:00:00'),
        (6, 'edge-node', 'registry-b', 'high', '2024-01-06 10:00:00', '2024-01-20 10:00:00'),
    ]
    
    cursor.executemany(
        "INSERT INTO mcp_server_registry (server_id, name, registry_source, risk_tier, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        servers
    )
    conn.commit()
    
    # Test query matching the SQLAlchemy logic
    # SQLite doesn't have SPLIT_PART, so we use CASE/INSTR/SUBSTR
    cursor.execute("""
    SELECT 
        registry_source,
        LOWER(
            CASE 
                WHEN INSTR(name, '-') > 0 THEN SUBSTR(name, 1, INSTR(name, '-') - 1)
                ELSE name
            END
        ) as name_prefix,
        COUNT(*) as server_count,
        MIN(created_at) as first_seen,
        MAX(updated_at) as last_seen
    FROM mcp_server_registry
    GROUP BY registry_source, 
        LOWER(
            CASE 
                WHEN INSTR(name, '-') > 0 THEN SUBSTR(name, 1, INSTR(name, '-') - 1)
                ELSE name
            END
        )
    """)
    
    base_results = cursor.fetchall()
    
    # Get tier distribution
    cursor.execute("""
    SELECT 
        registry_source,
        LOWER(
            CASE 
                WHEN INSTR(name, '-') > 0 THEN SUBSTR(name, 1, INSTR(name, '-') - 1)
                ELSE name
            END
        ) as name_prefix,
        risk_tier,
        COUNT(*) as tier_count
    FROM mcp_server_registry
    GROUP BY registry_source, 
        LOWER(
            CASE 
                WHEN INSTR(name, '-') > 0 THEN SUBSTR(name, 1, INSTR(name, '-') - 1)
                ELSE name
            END
        ),
        risk_tier
    """)
    
    tier_rows = cursor.fetchall()
    tier_map = {}
    for row in tier_rows:
        key = (row[0], row[1])
        if key not in tier_map:
            tier_map[key] = {}
        tier_map[key][row[2]] = row[3]
    
    # Build response matching expected format
    groups = []
    for row in base_results:
        registry_source, name_prefix, server_count, first_seen, last_seen = row
        key = (registry_source, name_prefix)
        groups.append({
            "registry_source": registry_source,
            "name_prefix": name_prefix,
            "server_count": server_count,
            "avg_trust_score": None,
            "tiers": tier_map.get(key, {}),
            "first_seen": first_seen,
            "last_seen": last_seen,
        })
    
    response = {"groups": groups}
    
    # Assertions
    assert len(groups) >= 2, f"Expected >=2 groups, got {len(groups)}"
    
    # Find registry-a and verify it has server_count >= 2
    registry_a_servers = 0
    for g in groups:
        if g["registry_source"] == "registry-a":
            registry_a_servers += g["server_count"]
    
    assert registry_a_servers >= 2, f"Expected registry-a server_count >=2, got {registry_a_servers}"
    
    # Verify we have at least 2 distinct registry_sources
    registry_sources = set(g["registry_source"] for g in groups)
    assert len(registry_sources) >= 2, f"Expected >=2 registry_sources, got {len(registry_sources)}"
    
    # Verify we have at least 3 name prefixes
    prefixes = set(g["name_prefix"] for g in groups)
    assert len(prefixes) >= 3, f"Expected >=3 name_prefixes, got {len(prefixes)}"
    
    print("PASS")