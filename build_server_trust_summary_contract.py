import os
import sys
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import requests

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
SERVICE_NAME = 'build_server_trust_summary_contract'
PROJECT_DIR = '/home/workspace/zo_sentinel'
CONTRACT_OUTPUT_PATH = os.path.join(PROJECT_DIR, 'contracts', 'server_trust_summary_contract.json')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> list[dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list[dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def compute_contract_id(name: str, version: str) -> str:
    content = f"{name}:{version}".encode('utf-8')
    return hashlib.sha256(content).hexdigest()[:16]


def build_trust_summary_contract() -> dict[str, Any]:
    contract = {
        'contract_id': compute_contract_id('server_trust_summary', '1.0.0'),
        'name': 'server_trust_summary',
        'version': '1.0.0',
        'description': 'Contract for server trust summary API endpoints and data structures',
        'created_at': utc_now_iso(),
        'endpoints': [
            {
                'path': '/api/v1/servers/{server_id}/trust-summary',
                'method': 'GET',
                'description': 'Get trust summary for a single server',
                'parameters': [
                    {
                        'name': 'server_id',
                        'type': 'string',
                        'required': True,
                        'description': 'Unique server identifier'
                    }
                ],
                'response': {
                    'server_id': {'type': 'string'},
                    'name': {'type': 'string'},
                    'url': {'type': 'string'},
                    'trust_score': {'type': 'float', 'min': 0.0, 'max': 100.0},
                    'verdict': {
                        'type': 'enum',
                        'values': ['TRUSTED', 'AMBER', 'UNTRUSTED', 'UNKNOWN', 'KNOWN_THREAT']
                    },
                    'risk_tier': {
                        'type': 'enum',
                        'values': ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
                    },
                    'threat_count': {'type': 'integer'},
                    'attestation_status': {'type': 'string'},
                    'signals': {
                        'type': 'object',
                        'properties': {
                            'supply_chain': {'type': 'float'},
                            'community': {'type': 'float'},
                            'temporal_stability': {'type': 'float'},
                            'permission_scope': {'type': 'float'},
                            'tool_description_safety': {'type': 'float'},
                            'injection_resilience': {'type': 'float'}
                        }
                    },
                    'summary_timestamp': {'type': 'string', 'format': 'ISO8601'}
                }
            },
            {
                'path': '/api/v1/servers/trust-summary/batch',
                'method': 'POST',
                'description': 'Get trust summaries for multiple servers',
                'parameters': [
                    {
                        'name': 'server_ids',
                        'type': 'array',
                        'items': {'type': 'string'},
                        'required': True,
                        'max_items': 100,
                        'description': 'List of server identifiers'
                    }
                ],
                'response': {
                    'type': 'array',
                    'items': {
                        'server_id': {'type': 'string'},
                        'name': {'type': 'string'},
                        'trust_score': {'type': 'float'},
                        'verdict': {'type': 'string'},
                        'risk_tier': {'type': 'string'},
                        'threat_count': {'type': 'integer'},
                        'signals': {'type': 'object'}
                    }
                }
            },
            {
                'path': '/api/v1/servers/trust-summary/top-risks',
                'method': 'GET',
                'description': 'Get top risk servers ranked by threat count and risk tier',
                'parameters': [
                    {
                        'name': 'limit',
                        'type': 'integer',
                        'required': False,
                        'default': 20,
                        'min': 1,
                        'max': 100
                    },
                    {
                        'name': 'risk_tier',
                        'type': 'string',
                        'required': False,
                        'description': 'Filter by risk tier'
                    }
                ],
                'response': {
                    'type': 'array',
                    'items': {
                        'server_id': {'type': 'string'},
                        'name': {'type': 'string'},
                        'trust_score': {'type': 'float'},
                        'risk_tier': {'type': 'string'},
                        'threat_count': {'type': 'integer'},
                        'verdict': {'type': 'string'}
                    }
                }
            },
            {
                'path': '/api/v1/servers/trust-summary/distribution',
                'method': 'GET',
                'description': 'Get distribution of verdicts and risk tiers across registry',
                'parameters': [],
                'response': {
                    'total_servers': {'type': 'integer'},
                    'verdict_distribution': {
                        'type': 'object',
                        'properties': {
                            'TRUSTED': {'type': 'integer'},
                            'AMBER': {'type': 'integer'},
                            'UNTRUSTED': {'type': 'integer'},
                            'UNKNOWN': {'type': 'integer'},
                            'KNOWN_THREAT': {'type': 'integer'}
                        }
                    },
                    'risk_tier_distribution': {
                        'type': 'object',
                        'properties': {
                            'LOW': {'type': 'integer'},
                            'MEDIUM': {'type': 'integer'},
                            'HIGH': {'type': 'integer'},
                            'CRITICAL': {'type': 'integer'}
                        }
                    },
                    'trust_score_histogram': {
                        'type': 'array',
                        'items': {
                            'bucket': {'type': 'string'},
                            'count': {'type': 'integer'}
                        }
                    }
                }
            }
        ],
        'data_sources': {
            'mcp_server_registry': {
                'description': 'Primary server registry with trust scores and verdicts',
                'columns_used': ['server_id', 'name', 'url', 'trust_score', 'verdict']
            },
            'mcp_risk_register': {
                'description': 'Risk tier and threat counts per server',
                'columns_used': ['server_id', 'risk_tier', 'threat_count']
            },
            'mcp_signal_scores': {
                'description': 'Individual signal scores per server',
                'columns_used': ['server_id', 'signal_name', 'score']
            },
            'mcp_attestations': {
                'description': 'Attestation status per server',
                'columns_used': ['server_id', 'status', 'attested_at']
            }
        },
        'query_templates': {
            'server_trust_summary': """
                SELECT
                    r.server_id,
                    r.name,
                    r.url,
                    r.trust_score,
                    r.verdict,
                    COALESCE(risk.risk_tier, 'UNKNOWN') as risk_tier,
                    COALESCE(risk.threat_count, 0) as threat_count,
                    (SELECT status FROM mcp_attestations WHERE server_id = r.server_id ORDER BY attested_at DESC LIMIT 1) as attestation_status,
                    r.last_assessed
                FROM mcp_server_registry r
                LEFT JOIN mcp_risk_register risk ON r.server_id = risk.server_id
                WHERE r.server_id = ?
            """,
            'batch_trust_summary': """
                SELECT
                    r.server_id,
                    r.name,
                    r.url,
                    r.trust_score,
                    r.verdict,
                    COALESCE(risk.risk_tier, 'UNKNOWN') as risk_tier,
                    COALESCE(risk.threat_count, 0) as threat_count
                FROM mcp_server_registry r
                LEFT JOIN mcp_risk_register risk ON r.server_id = risk.server_id
                WHERE r.server_id IN ({server_ids})
            """,
            'signal_scores': """
                SELECT server_id, signal_name, score
                FROM mcp_signal_scores
                WHERE server_id IN ({server_ids})
                AND scored_at > NOW() - INTERVAL '30 days'
            """,
            'top_risks': """
                SELECT
                    r.server_id,
                    r.name,
                    r.trust_score,
                    r.verdict,
                    risk.risk_tier,
                    risk.threat_count
                FROM mcp_server_registry r
                JOIN mcp_risk_register risk ON r.server_id = risk.server_id
                WHERE risk.risk_tier IN ('HIGH', 'CRITICAL')
                ORDER BY risk.threat_count DESC, risk.risk_tier DESC
                LIMIT ?
            """,
            'distribution': """
                SELECT
                    verdict,
                    COUNT(*) as count
                FROM mcp_server_registry
                GROUP BY verdict
            """
        },
        'validation_rules': {
            'trust_score': {
                'type': 'float',
                'min': 0.0,
                'max': 100.0,
                'required': True
            },
            'verdict': {
                'type': 'enum',
                'values': ['TRUSTED', 'AMBER', 'UNTRUSTED', 'UNKNOWN', 'KNOWN_THREAT'],
                'required': True
            },
            'risk_tier': {
                'type': 'enum',
                'values': ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'],
                'required': False,
                'default': 'UNKNOWN'
            }
        },
        'caching': {
            'enabled': True,
            'ttl_seconds': 300,
            'strategy': 'cache_by_server_id',
            'invalidation_events': ['verdict_change', 'risk_tier_change', 'new_signal_score']
        },
        'rate_limits': {
            'single_server': {'requests_per_minute': 60},
            'batch': {'requests_per_minute': 10},
            'distribution': {'requests_per_minute': 5}
        },
        'error_codes': {
            'SERVER_NOT_FOUND': {'http_status': 404, 'message': 'Server not found in registry'},
            'INVALID_SERVER_ID': {'http_status': 400, 'message': 'Invalid server ID format'},
            'RATE_LIMIT_EXCEEDED': {'http_status': 429, 'message': 'Rate limit exceeded'},
            'BATCH_SIZE_EXCEEDED': {'http_status': 400, 'message': 'Batch size exceeds maximum of 100'},
            'INTERNAL_ERROR': {'http_status': 500, 'message': 'Internal server error'}
        }
    }
    return contract


def ensure_contracts_directory() -> bool:
    contracts_dir = os.path.join(PROJECT_DIR, 'contracts')
    try:
        os.makedirs(contracts_dir, exist_ok=True)
        return True
    except Exception as e:
        log.error(f"Failed to create contracts directory: {e}")
        return False


def write_contract_file(contract: dict[str, Any]) -> bool:
    try:
        ensure_contracts_directory()
        with open(CONTRACT_OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(contract, f, indent=2)
        log.info(f"Contract written to {CONTRACT_OUTPUT_PATH}")
        return True
    except Exception as e:
        log.error(f"Failed to write contract file: {e}")
        return False


def create_contract_table() -> bool:
    sql = """
        CREATE TABLE IF NOT EXISTS api_contracts (
            contract_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            description TEXT,
            schema_version VARCHAR,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            endpoints_count INTEGER,
            data_sources TEXT,
            validation_rules TEXT,
            is_active BOOLEAN DEFAULT true
        )
    """
    return ws_execute(sql)


def register_contract_in_db(contract: dict[str, Any]) -> bool:
    sql = """
        INSERT INTO api_contracts (
            contract_id, name, version, description, schema_version,
            created_at, updated_at, endpoints_count, data_sources,
            validation_rules, is_active
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT (contract_id) DO UPDATE SET
            description = EXCLUDED.description,
            updated_at = EXCLUDED.updated_at,
            endpoints_count = EXCLUDED.endpoints_count,
            data_sources = EXCLUDED.data_sources,
            validation_rules = EXCLUDED.validation_rules
    """
    row = {
        'contract_id': contract['contract_id'],
        'name': contract['name'],
        'version': contract['version'],
        'description': contract['description'],
        'schema_version': '1.0',
        'created_at': contract['created_at'],
        'updated_at': utc_now_iso(),
        'endpoints_count': len(contract.get('endpoints', [])),
        'data_sources': json.dumps(contract.get('data_sources', {})),
        'validation_rules': json.dumps(contract.get('validation_rules', {})),
        'is_active': True
    }
    return ws_write('api_contracts', [row])


def validate_contract_against_db(contract: dict[str, Any]) -> bool:
    sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'mcp_server_registry'
    """
    registry_columns = ws_query(sql)
    column_names = {col['column_name'] for col in registry_columns}
    
    required_columns = {'server_id', 'name', 'url', 'trust_score', 'verdict'}
    missing = required_columns - column_names
    if missing:
        log.warning(f"Registry missing columns: {missing}")
        return False
    
    sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'mcp_risk_register'
    """
    risk_columns = ws_query(sql)
    risk_column_names = {col['column_name'] for col in risk_columns}
    
    risk_required = {'server_id', 'risk_tier', 'threat_count'}
    risk_missing = risk_required - risk_column_names
    if risk_missing:
        log.warning(f"Risk register missing columns: {risk_missing}")
        return False
    
    sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'mcp_signal_scores'
    """
    signal_columns = ws_query(sql)
    signal_column_names = {col['column_name'] for col in signal_columns}
    
    signal_required = {'server_id', 'signal_name', 'score'}
    signal_missing = signal_required - signal_column_names
    if signal_missing:
        log.warning(f"Signal scores missing columns: {signal_missing}")
        return False
    
    log.info("Contract validation passed against live schema")
    return True


def generate_implementation_skeleton() -> dict[str, str]:
    skeleton = {
        'trust_summary_api.py': '''"""
Server Trust Summary API
Auto-generated from contract: server_trust_summary v1.0.0
"""
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
import requests
import logging

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
PORT = 8790

app = FastAPI(title="Server Trust Summary API", version="1.0.0")
log = logging.getLogger(__name__)


class TrustSummary(BaseModel):
    server_id: str
    name: str
    url: str
    trust_score: float = Field(ge=0.0, le=100.0)
    verdict: str
    risk_tier: str = "UNKNOWN"
    threat_count: int = 0
    attestation_status: Optional[str] = None
    signals: dict[str, float] = {}
    summary_timestamp: str


class BatchTrustSummaryRequest(BaseModel):
    server_ids: list[str] = Field(..., max_items=100)


def ws_query(sql: str) -> list[dict]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


@app.get("/health")
async def health():
    return {"status": "ok", "service": "trust_summary_api", "version": "1.0.0"}


@app.get("/api/v1/servers/{server_id}/trust-summary", response_model=TrustSummary)
async def get_trust_summary(server_id: str):
    sql = """
        SELECT
            r.server_id, r.name, r.url, r.trust_score, r.verdict,
            COALESCE(risk.risk_tier, 'UNKNOWN') as risk_tier,
            COALESCE(risk.threat_count, 0) as threat_count,
            (SELECT status FROM mcp_attestations WHERE server_id = r.server_id ORDER BY attested_at DESC LIMIT 1) as attestation_status
        FROM mcp_server_registry r
        LEFT JOIN mcp_risk_register risk ON r.server_id = risk.server_id
        WHERE r.server_id = ?
    """
    rows = ws_query(sql.replace("?", f"'{server_id}'"))
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found in registry")
    row = rows[0]
    row["summary_timestamp"] = datetime.utcnow().isoformat() + "Z"
    row["signals"] = {}
    return row


def run():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()
'''
    }
    return skeleton


def write_implementation_skeleton() -> bool:
    skeleton = generate_implementation_skeleton()
    impl_dir = os.path.join(PROJECT_DIR, 'impl_skeletons')
    try:
        os.makedirs(impl_dir, exist_ok=True)
        for filename, content in skeleton.items():
            filepath = os.path.join(impl_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            log.info(f"Wrote implementation skeleton: {filepath}")
        return True
    except Exception as e:
        log.error(f"Failed to write implementation skeleton: {e}")
        return False


def send_heartbeat() -> bool:
    row = {
        'service': SERVICE_NAME,
        'status': 'running',
        'ts': utc_now_iso(),
        'meta': json.dumps({
            'contract_id': compute_contract_id('server_trust_summary', '1.0.0'),
            'endpoints': 4,
            'version': '1.0.0'
        })
    }
    return ws_write('service_health', [row])


def run() -> None:
    log.info(f"Starting {SERVICE_NAME}")
    
    try:
        send_heartbeat()
    except Exception as e:
        log.warning(f"Heartbeat failed (write service may not be running): {e}")
    
    log.info("Building server trust summary contract")
    contract = build_trust_summary_contract()
    
    log.info("Validating contract against live database schema")
    is_valid = validate_contract_against_db(contract)
    if not is_valid:
        log.warning("Contract validation found schema discrepancies - contract still generated")
    
    log.info("Creating contract table if not exists")
    create_contract_table()
    
    log.info("Writing contract file")
    write_contract_file(contract)
    
    log.info("Registering contract in database")
    register_contract_in_db(contract)
    
    log.info("Writing implementation skeleton")
    write_implementation_skeleton()
    
    log.info(f"Contract build complete: {contract['contract_id']}")
    log.info(f"Contract file: {CONTRACT_OUTPUT_PATH}")
    log.info(f"Endpoints defined: {len(contract['endpoints'])}")
    log.info(f"Data sources: {list(contract['data_sources'].keys())}")


if __name__ == '__main__':
    run()
    sys.exit(0)