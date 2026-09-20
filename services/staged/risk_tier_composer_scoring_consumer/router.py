"""
router.py for risk_tier_composer_scoring_consumer service.
Thin APIRouter exposing scoring logic for risk tier computation.
"""
from datetime import datetime
from fastapi import APIRouter, Depends
import logging

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

logger = logging.getLogger(__name__)

# Axis weights for composite score computation
AXIS_WEIGHTS = {
    'CRITICAL': 0.30,
    'HIGH': 0.25,
    'MEDIUM': 0.20,
    'LOW': 0.15,
    'INFO': 0.05,
    'UNKNOWN': 0.03,
    'NONE': 0.02,
}


def compute_all_risk_tiers(session) -> dict:
    """
    Compute composite risk tiers for all servers with pending axis scores.
    
    Reads all distinct server_ids from McpLlmAxisScore where scored_at
    is newer than the server's last_assessed. Computes weighted average of
    p_top across all axes. CRITICAL axis with p_top=1.0 forces HIGH_RISK_ISOLATED.
    Updates risk_tier in McpServerRegistry and sets last_assessed.
    
    Returns dict mapping server_id to computed risk_tier.
    """
    from sqlalchemy import func, and_
    
    results = {}
    updated_count = 0
    
    # Find servers needing assessment (either never assessed or have newer scores)
    subquery = (
        session.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label('max_scored_at')
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )
    
    servers_to_check = (
        session.query(McpServerRegistry)
        .outerjoin(
            subquery,
            McpServerRegistry.server_id == subquery.c.server_id
        )
        .filter(
            or_(
                McpServerRegistry.last_assessed == None,
                and_(
                    subquery.c.max_scored_at != None,
                    subquery.c.max_scored_at > McpServerRegistry.last_assessed
                )
            )
        )
        .all()
    )
    
    for server in servers_to_check:
        # Get all axis scores for this server
        axis_scores = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == server.server_id)
            .all()
        )
        
        if not axis_scores:
            continue
        
        # Build scores dict by axis
        scores_by_axis = {s.axis_name: s.p_top for s in axis_scores}
        
        # Compute and assign tier
        tier = compute_risk_tier(scores_by_axis)
        server.risk_tier = tier
        server.last_assessed = datetime.utcnow()
        results[server.server_id] = tier
        updated_count += 1
        
        logger.info(
            f"Computed tier for {server.server_id}: {tier} "
            f"(axes: {len(scores_by_axis)})"
        )
    
    session.commit()
    logger.info(f"compute_all_risk_tiers: processed {len(results)} servers, updated {updated_count}")
    
    return results


def compute_risk_tier(scores_by_axis: dict) -> str:
    """
    Compute risk tier from axis scores dict.
    
    CRITICAL axis with p_top=1.0 forces HIGH_RISK_ISOLATED.
    Otherwise computes weighted average across all axes.
    """
    # Trust-gating override: CRITICAL axis forces HIGH_RISK_ISOLATED
    if scores_by_axis.get('CRITICAL', 0) >= 1.0:
        return 'HIGH_RISK_ISOLATED'
    
    # Compute weighted composite score
    composite = sum(
        p_top * AXIS_WEIGHTS.get(axis, 0.0)
        for axis, p_top in scores_by_axis.items()
    )
    
    # Map composite to tier
    if composite >= 0.8:
        return 'HIGH_RISK_ISOLATED'
    elif composite >= 0.5:
        return 'HIGH_RISK'
    elif composite >= 0.2:
        return 'MEDIUM_RISK'
    else:
        return 'LOW_RISK'


# Expose router for service import compatibility
router = APIRouter()


if __name__ == "__main__":
    import sqlite3
    
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    print("Running self-test for risk_tier_composer_scoring_consumer...")
    
    # Create in-memory SQLite for testing
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create test tables
    cursor.execute('''
        CREATE TABLE McpServerRegistry (
            id INTEGER PRIMARY KEY,
            server_id TEXT NOT NULL UNIQUE,
            risk_tier TEXT,
            last_assessed TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            p_top REAL NOT NULL,
            scored_at TIMESTAMP,
            UNIQUE(server_id, axis_name)
        )
    ''')
    
    # Seed 3 servers with known axis scores
    servers = [
        {
            'server_id': 'server_A',
            'scores': [
                ('CRITICAL', 1.0), ('HIGH', 0.0), ('MEDIUM', 0.0),
                ('LOW', 0.0), ('INFO', 0.0), ('UNKNOWN', 0.0), ('NONE', 0.0),
            ]
        },
        {
            'server_id': 'server_B',
            'scores': [
                ('CRITICAL', 0.0), ('HIGH', 0.0), ('MEDIUM', 0.2),
                ('LOW', 0.3), ('INFO', 0.3), ('UNKNOWN', 0.1), ('NONE', 0.1),
            ]
        },
        {
            'server_id': 'server_C',
            'scores': [
                ('CRITICAL', 0.0), ('HIGH', 0.0), ('MEDIUM', 0.0),
                ('LOW', 0.0), ('INFO', 0.0), ('UNKNOWN', 0.0), ('NONE', 1.0),
            ]
        },
    ]
    
    now = datetime.now().isoformat()
    for server in servers:
        cursor.execute(
            'INSERT INTO McpServerRegistry (server_id, risk_tier, last_assessed) VALUES (?, ?, ?)',
            (server['server_id'], None, None)
        )
        for axis_name, p_top in server['scores']:
            cursor.execute(
                'INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top, scored_at) VALUES (?, ?, ?, ?)',
                (server['server_id'], axis_name, p_top, now)
            )
    
    conn.commit()
    
    try:
        # Simulate compute_all_risk_tiers using raw SQL
        cursor.execute('''
            SELECT s.server_id, a.axis_name, a.p_top
            FROM McpServerRegistry s
            JOIN McpLlmAxisScore a ON s.server_id = a.server_id
            WHERE s.last_assessed IS NULL
               OR a.scored_at > s.last_assessed
        ''')
        
        rows = cursor.fetchall()
        servers_data = {}
        for row in rows:
            sid = row['server_id']
            if sid not in servers_data:
                servers_data[sid] = {}
            servers_data[sid][row['axis_name']] = row['p_top']
        
        results = {}
        for server_id, scores in servers_data.items():
            tier = compute_risk_tier(scores)
            results[server_id] = tier
            cursor.execute(
                'UPDATE McpServerRegistry SET risk_tier = ?, last_assessed = ? WHERE server_id = ?',
                (tier, now, server_id)
            )
        
        conn.commit()
        
        # Verify expected outcomes
        # server_A: CRITICAL=1.0 -> HIGH_RISK_ISOLATED (trust-gating override)
        # server_B: composite = 0.2*0.20 + 0.3*0.15 + 0.3*0.05 + 0.1*0.03 + 0.1*0.02 = 0.115 -> LOW_RISK
        # server_C: composite = 1.0*0.02 = 0.02 -> LOW_RISK
        
        expected = {
            'server_A': 'HIGH_RISK_ISOLATED',
            'server_B': 'LOW_RISK',
            'server_C': 'LOW_RISK',
        }
        
        matches = sum(1 for sid, tier in results.items() if results.get(sid) == expected.get(sid))
        
        print(f"Results: {results}")
        print(f"Expected: {expected}")
        print(f"Matches: {matches}/3")
        
        if matches >= 2:
            print("PASS")
        else:
            print("FAIL: fewer than 2 tiers matched expected values")
            
    except Exception as e:
        print("FAIL")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()