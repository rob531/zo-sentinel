from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
import requests
from app.db import get_session
from app.models import MCPLLMAxisScore

app = FastAPI()

class AuditResult(BaseModel):
    servers_checked: int
    consistent_servers: int
    inconsistent_count: int
    inconsistent: List[Dict]

class QueryParams(BaseModel):
    limit: Optional[int] = 500

def _audit(rows: List[dict]) -> Dict:
    servers = {}

    for row in rows:
        server_id = row['server_id']
        if server_id not in servers:
            servers[server_id] = {
                'versions_seen': set(),
                'models_seen': set(),
                'adapters_seen': set(),
                'axes_per_version': {},
                'axes_total': 0,
                'axes_unique': set(),
                'scored_at_min': None,
                'scored_at_max': None
            }

        server = servers[server_id]
        server['versions_seen'].add(row['decision_rule_version'])
        server['models_seen'].add(row['model_version'])
        server['adapters_seen'].add(row['adapter_sha256'])
        server['axes_total'] += 1
        server['axes_unique'].add(row['axis_name'])

        version_key = f"{row['decision_rule_version']}_{row['model_version']}_{row['adapter_sha256']}"
        if version_key not in server['axes_per_version']:
            server['axes_per_version'][version_key] = []
        server['axes_per_version'][version_key].append(row['axis_name'])

        if server['scored_at_min'] is None or row['scored_at'] < server['scored_at_min']:
            server['scored_at_min'] = row['scored_at']
        if server['scored_at_max'] is None or row['scored_at'] > server['scored_at_max']:
            server['scored_at_max'] = row['scored_at']

    inconsistent_servers = []
    consistent_count = 0

    for server_id, server in servers.items():
        if (len(server['versions_seen']) == 1 and
            len(server['models_seen']) == 1 and
            len(server['adapters_seen']) == 1):
            consistent_count += 1
        else:
            inconsistent_servers.append({
                'server_id': server_id,
                'versions_seen': list(server['versions_seen']),
                'models_seen': list(server['models_seen']),
                'adapters_seen': list(server['adapters_seen']),
                'axes_per_version': server['axes_per_version'],
                'axes_total': server['axes_total'],
                'axes_unique': len(server['axes_unique']),
                'scored_at_min': server['scored_at_min'].isoformat(),
                'scored_at_max': server['scored_at_max'].isoformat()
            })

    return {
        'servers_checked': len(servers),
        'consistent_servers': consistent_count,
        'inconsistent_count': len(inconsistent_servers),
        'inconsistent': inconsistent_servers
    }

@app.get('/audit/axis-pairwise-consistency')
async def audit_axis_pairwise_consistency(limit: int = 500):
    if limit < 1 or limit > 5000:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 5000")

    try:
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={
                'sql': 'SELECT server_id, axis_name, decision_rule_version, model_version, adapter_sha256, scored_at FROM mcp_llm_axis_scores ORDER BY scored_at DESC LIMIT ?',
                'params': [limit]
            },
            timeout=10
        )
        response.raise_for_status()
        rows = response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error querying database: {str(e)}")

    return _audit(rows)

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create fake data
    fake_rows = [
        {'server_id': 'server_a', 'axis_name': 'axis1', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_a', 'axis_name': 'axis2', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_a', 'axis_name': 'axis3', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_a', 'axis_name': 'axis4', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_a', 'axis_name': 'axis5', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_a', 'axis_name': 'axis6', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_a', 'axis_name': 'axis7', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_b', 'axis_name': 'axis1', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_b', 'axis_name': 'axis2', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_b', 'axis_name': 'axis3', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_b', 'axis_name': 'axis4', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_b', 'axis_name': 'axis5', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_b', 'axis_name': 'axis6', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_b', 'axis_name': 'axis7', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_c', 'axis_name': 'axis1', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_c', 'axis_name': 'axis2', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_c', 'axis_name': 'axis3', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_c', 'axis_name': 'axis4', 'decision_rule_version': 'v1', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_c', 'axis_name': 'axis5', 'decision_rule_version': 'v2', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_c', 'axis_name': 'axis6', 'decision_rule_version': 'v2', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
        {'server_id': 'server_c', 'axis_name': 'axis7', 'decision_rule_version': 'v2', 'model_version': 'm1', 'adapter_sha256': 'a1', 'scored_at': datetime.now()},
    ]

    result = _audit(fake_rows)
    assert result['inconsistent_count'] == 1
    assert result['inconsistent'][0]['server_id'] == 'server_c'
    assert 'v1' in result['inconsistent'][0]['versions_seen']
    assert 'v2' in result['inconsistent'][0]['versions_seen']

    print('PASS')