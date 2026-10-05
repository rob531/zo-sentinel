# services/staged/axis_critical_multi_flags/logic.py
"""
Axis Critical Multi-Flags Service Logic

Provides functions for identifying servers with critical multi-flag conditions.
Mirrors the _exemplar pattern for data/computation services.
"""

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


def get_critical_flagged_servers():
    """
    Get servers that have multiple critical flags.
    Returns servers with their critical flag information.
    """
    session = get_session()
    try:
        # Query servers with critical flags
        query = """
            SELECT 
                msr.server_id,
                msr.name as server_name,
                msr.designation,
                msr.flag_state,
                msr.confidence,
                mlias.alignment_score,
                mlias.safety_score,
                mlias.utility_score
            FROM mcp_server_registry msr
            LEFT JOIN mcp_llm_axis_scores mlias ON msr.server_id = mlias.server_id
            WHERE msr.flag_state IN ('critical', 'warning', 'review')
            ORDER BY msr.confidence DESC, msr.server_id
        """
        result = session.execute(query)
        rows = result.fetchall()
        
        servers = []
        for row in rows:
            servers.append({
                'server_id': row[0],
                'server_name': row[1],
                'designation': row[2],
                'flag_state': row[3],
                'confidence': row[4],
                'alignment_score': row[5],
                'safety_score': row[6],
                'utility_score': row[7]
            })
        return servers
    finally:
        session.close()


def get_servers_by_flag_state(flag_state: str):
    """
    Get servers filtered by specific flag state.
    
    Args:
        flag_state: The flag state to filter by ('critical', 'warning', 'review', 'clear')
    
    Returns:
        List of servers with the specified flag state
    """
    session = get_session()
    try:
        query = """
            SELECT 
                server_id,
                name,
                designation,
                flag_state,
                confidence,
                updated_at
            FROM mcp_server_registry
            WHERE flag_state = :flag_state
            ORDER BY confidence DESC
        """
        result = session.execute(query, {'flag_state': flag_state})
        rows = result.fetchall()
        
        servers = []
        for row in rows:
            servers.append({
                'server_id': row[0],
                'name': row[1],
                'designation': row[2],
                'flag_state': row[3],
                'confidence': row[4],
                'updated_at': row[5]
            })
        return servers
    finally:
        session.close()


def get_multi_flag_servers(min_flags: int = 2):
    """
    Get servers that have multiple flags set.
    
    Args:
        min_flags: Minimum number of flags to consider as 'multi-flag'
    
    Returns:
        List of servers with multiple flags
    """
    session = get_session()
    try:
        # This would need actual multi-flag logic
        # For now, query servers with any non-clear flag states
        query = """
            SELECT 
                server_id,
                name,
                designation,
                flag_state,
                confidence,
                COALESCE(
                    CASE WHEN flag_state = 'critical' THEN 1 ELSE 0 END +
                    CASE WHEN confidence < 0.5 THEN 1 ELSE 0 END,
                    0
                ) as flag_count
            FROM mcp_server_registry
            WHERE flag_state != 'clear'
            AND flag_state IS NOT NULL
            ORDER BY flag_count DESC, confidence DESC
        """
        result = session.execute(query)
        rows = result.fetchall()
        
        servers = []
        for row in rows:
            servers.append({
                'server_id': row[0],
                'name': row[1],
                'designation': row[2],
                'flag_state': row[3],
                'confidence': row[4],
                'flag_count': row[5]
            })
        
        # Filter to those meeting min_flags threshold
        return [s for s in servers if s['flag_count'] >= min_flags]
    finally:
        session.close()


def get_axis_scores_summary():
    """
    Get summary of axis scores across all servers.
    
    Returns:
        Summary statistics of alignment, safety, and utility scores
    """
    session = get_session()
    try:
        query = """
            SELECT 
                COUNT(*) as total_servers,
                AVG(alignment_score) as avg_alignment,
                AVG(safety_score) as avg_safety,
                AVG(utility_score) as avg_utility,
                MIN(alignment_score) as min_alignment,
                MAX(alignment_score) as max_alignment,
                MIN(safety_score) as min_safety,
                MAX(safety_score) as max_safety
            FROM mcp_llm_axis_scores
            WHERE alignment_score IS NOT NULL
        """
        result = session.execute(query)
        row = result.fetchone()
        
        return {
            'total_servers': row[0],
            'avg_alignment': row[1],
            'avg_safety': row[2],
            'avg_utility': row[3],
            'min_alignment': row[4],
            'max_alignment': row[5],
            'min_safety': row[6],
            'max_safety': row[7]
        }
    finally:
        session.close()


def _run_self_test():
    """
    Self-test function for service validation.
    Tests connectivity and basic queries.
    
    Returns:
        dict with test results
    """
    results = {
        'tests': [],
        'passed': 0,
        'failed': 0
    }
    
    # Test 1: Get session
    try:
        session = get_session()
        session.close()
        results['tests'].append({
            'name': 'database_connection',
            'status': 'PASS',
            'message': 'Successfully connected to database'
        })
        results['passed'] += 1
    except Exception as e:
        results['tests'].append({
            'name': 'database_connection',
            'status': 'FAIL',
            'message': str(e)
        })
        results['failed'] += 1
    
    # Test 2: Query servers
    try:
        servers = get_critical_flagged_servers()
        results['tests'].append({
            'name': 'get_critical_flagged_servers',
            'status': 'PASS',
            'message': f'Retrieved {len(servers)} servers'
        })
        results['passed'] += 1
    except Exception as e:
        results['tests'].append({
            'name': 'get_critical_flagged_servers',
            'status': 'FAIL',
            'message': str(e)
        })
        results['failed'] += 1
    
    # Test 3: Query by flag state
    try:
        servers = get_servers_by_flag_state('critical')
        results['tests'].append({
            'name': 'get_servers_by_flag_state',
            'status': 'PASS',
            'message': f'Retrieved {len(servers)} critical servers'
        })
        results['passed'] += 1
    except Exception as e:
        results['tests'].append({
            'name': 'get_servers_by_flag_state',
            'status': 'FAIL',
            'message': str(e)
        })
        results['failed'] += 1
    
    # Test 4: Axis scores summary
    try:
        summary = get_axis_scores_summary()
        results['tests'].append({
            'name': 'get_axis_scores_summary',
            'status': 'PASS',
            'message': f'Summary: {summary.get("total_servers", 0)} servers'
        })
        results['passed'] += 1
    except Exception as e:
        results['tests'].append({
            'name': 'get_axis_scores_summary',
            'status': 'FAIL',
            'message': str(e)
        })
        results['failed'] += 1
    
    return results


if __name__ == '__main__':
    print("Running axis_critical_multi_flags self-test...")
    print("-" * 50)
    
    results = _run_self_test()
    
    for test in results['tests']:
        status_symbol = "✓" if test['status'] == 'PASS' else "✗"
        print(f"{status_symbol} {test['name']}: {test['status']}")
        print(f"  {test['message']}")
    
    print("-" * 50)
    print(f"Results: {results['passed']} passed, {results['failed']} failed")
    
    if results['failed'] == 0:
        print("PASS")
    else:
        print("FAIL")