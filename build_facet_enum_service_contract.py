import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'facet_enum'
PROJECT_DIR = '/home/workspace/zo_sentinel'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
SERVICE_PORT = 8792
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

FACET_ENUM_TABLE = 'facet_enums'
FACET_VALUES_TABLE = 'facet_values'

REQUIRED_FACET_COLUMNS = [
    'facet_name',
    'description',
    'created_at',
    'updated_at',
]

REQUIRED_VALUE_COLUMNS = [
    'facet_name',
    'enum_value',
    'display_label',
    'sort_order',
    'is_active',
    'created_at',
    'updated_at',
]

FACET_NAMES = [
    'verdict',
    'risk_tier',
    'signal_type',
    'registry_source',
    'attestation_status',
    'submission_source',
    'threat_severity',
    'scan_status',
    'approval_status',
    'compliance_status',
]

DEFAULT_FACET_ENUMS = {
    'verdict': [
        ('TRUSTED', 'Trusted', 1),
        ('AMBER', 'Amber - Use Caution', 2),
        ('UNTRUSTED', 'Untrusted', 3),
        ('UNKNOWN', 'Unknown', 4),
        ('KNOWN_THREAT', 'Known Threat', 5),
        ('HIGH_RISK_ISOLATED', 'High Risk - Isolated', 6),
        ('CAUTION_LIMITED', 'Caution - Limited Data', 7),
        ('TRUSTED_RESEARCH', 'Trusted - Research', 8),
        ('ENTERPRISE_CONTROLLED', 'Enterprise Controlled', 9),
    ],
    'risk_tier': [
        ('TIER_1_CRITICAL', 'Tier 1 - Critical', 1),
        ('TIER_2_HIGH', 'Tier 2 - High', 2),
        ('TIER_3_ELEVATED', 'Tier 3 - Elevated', 3),
        ('TIER_4_MODERATE', 'Tier 4 - Moderate', 4),
        ('TIER_5_LOW', 'Tier 5 - Low', 5),
        ('TIER_6_MINIMAL', 'Tier 6 - Minimal', 6),
    ],
    'signal_type': [
        ('supply_chain', 'Supply Chain Signal', 1),
        ('community_signal', 'Community Signal', 2),
        ('temporal_stability', 'Temporal Stability Signal', 3),
        ('permission_scope', 'Permission Scope Signal', 4),
        ('tool_description', 'Tool Description Signal', 5),
        ('injection_resilience', 'Injection Resilience Signal', 6),
        ('context_efficiency', 'Context Efficiency Signal', 7),
        ('domain_trust', 'Domain Trust Signal', 8),
        ('registry_breadth', 'Registry Breadth Signal', 9),
        ('evidence_density', 'Evidence Density Signal', 10),
        ('vendor_concentration', 'Vendor Concentration Signal', 11),
        ('traffic_fingerprint', 'Traffic Fingerprint Signal', 12),
    ],
    'registry_source': [
        ('npm', 'NPM Registry', 1),
        ('github', 'GitHub', 2),
        ('smithery', 'Smithery AI', 3),
        ('toolbench', 'Arcade Toolbench', 4),
        ('manual', 'Manual Submission', 5),
        ('directory', 'MCP Directory', 6),
    ],
    'attestation_status': [
        ('none', 'No Attestation', 1),
        ('pending', 'Attestation Pending', 2),
        ('verified', 'Verified', 3),
        ('expired', 'Expired', 4),
        ('revoked', 'Revoked', 5),
    ],
    'submission_source': [
        ('user', 'User Submission', 1),
        ('auto_discovery', 'Auto Discovery', 2),
        ('api', 'API Submission', 3),
        ('bulk_import', 'Bulk Import', 4),
    ],
    'threat_severity': [
        ('critical', 'Critical', 1),
        ('high', 'High', 2),
        ('medium', 'Medium', 3),
        ('low', 'Low', 4),
        ('info', 'Informational', 5),
    ],
    'scan_status': [
        ('pending', 'Pending', 1),
        ('in_progress', 'In Progress', 2),
        ('completed', 'Completed', 3),
        ('failed', 'Failed', 4),
        ('timeout', 'Timeout', 5),
    ],
    'approval_status': [
        ('pending', 'Pending Review', 1),
        ('approved', 'Approved', 2),
        ('rejected', 'Rejected', 3),
        ('escalated', 'Escalated', 4),
        ('exempted', 'Exempted', 5),
    ],
    'compliance_status': [
        ('compliant', 'Compliant', 1),
        ('non_compliant', 'Non-Compliant', 2),
        ('pending_review', 'Pending Review', 3),
        ('not_applicable', 'Not Applicable', 4),
    ],
}


def ws_query(sql):
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def create_facet_enums_table():
    logger.info('Creating facet_enums table if not exists')
    ws_execute(f'''
        CREATE TABLE IF NOT EXISTS {FACET_ENUM_TABLE} (
            facet_name VARCHAR PRIMARY KEY,
            description TEXT,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
    ''')


def create_facet_values_table():
    logger.info('Creating facet_values table if not exists')
    ws_execute(f'''
        CREATE TABLE IF NOT EXISTS {FACET_VALUES_TABLE} (
            id INTEGER PRIMARY KEY,
            facet_name VARCHAR NOT NULL,
            enum_value VARCHAR NOT NULL,
            display_label VARCHAR NOT NULL,
            sort_order INTEGER,
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            UNIQUE(facet_name, enum_value)
        )
    ''')


def ensure_tables():
    create_facet_enums_table()
    create_facet_values_table()


def seed_facet_enums():
    logger.info('Seeding facet_enums definitions')
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for facet_name in FACET_NAMES:
        rows = ws_query(f"SELECT 1 FROM {FACET_ENUM_TABLE} WHERE facet_name = '{facet_name}'")
        if not rows:
            description = f'Enumeration values for {facet_name} dimension'
            ws_write(FACET_ENUM_TABLE, [{
                'facet_name': facet_name,
                'description': description,
                'created_at': now,
                'updated_at': now,
            }])
            logger.info(f'Seeded facet: {facet_name}')


def seed_facet_values():
    logger.info('Seeding facet_values')
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for facet_name, values in DEFAULT_FACET_ENUMS.items():
        for idx, (enum_value, display_label, sort_order) in enumerate(values):
            existing = ws_query(
                f"SELECT 1 FROM {FACET_VALUES_TABLE} WHERE facet_name = '{facet_name}' AND enum_value = '{enum_value}'"
            )
            if not existing:
                ws_write(FACET_VALUES_TABLE, [{
                    'id': idx + 1,
                    'facet_name': facet_name,
                    'enum_value': enum_value,
                    'display_label': display_label,
                    'sort_order': sort_order,
                    'is_active': True,
                    'created_at': now,
                    'updated_at': now,
                }])
                logger.info(f'Seeded {facet_name}.{enum_value}')


def verify_schema():
    logger.info('Verifying facet_enums schema')
    cols = ws_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'facet_enums'"
    )
    col_names = [r['column_name'] for r in cols]
    for req in REQUIRED_FACET_COLUMNS:
        if req not in col_names:
            raise ValueError(f'Missing required column in facet_enums: {req}')
    logger.info('facet_enums schema OK')

    logger.info('Verifying facet_values schema')
    cols = ws_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'facet_values'"
    )
    col_names = [r['column_name'] for r in cols]
    for req in REQUIRED_VALUE_COLUMNS:
        if req not in col_names:
            raise ValueError(f'Missing required column in facet_values: {req}')
    logger.info('facet_values schema OK')


def get_facet_values(facet_name):
    logger.info(f'Retrieving enum values for facet: {facet_name}')
    return ws_query(
        f"SELECT * FROM {FACET_VALUES_TABLE} WHERE facet_name = '{facet_name}' AND is_active = true ORDER BY sort_order"
    )


def get_all_facets():
    logger.info('Retrieving all facet definitions')
    return ws_query(f'SELECT * FROM {FACET_ENUM_TABLE}')


def check_enum_value_exists(facet_name, enum_value):
    rows = ws_query(
        f"SELECT 1 FROM {FACET_VALUES_TABLE} WHERE facet_name = '{facet_name}' AND enum_value = '{enum_value}'"
    )
    return bool(rows)


def add_enum_value(facet_name, enum_value, display_label, sort_order=None):
    logger.info(f'Adding enum value {facet_name}.{enum_value}')
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    if sort_order is None:
        existing = ws_query(
            f'SELECT MAX(sort_order) as max_order FROM {FACET_VALUES_TABLE} WHERE facet_name = \'{facet_name}\''
        )
        sort_order = (existing[0].get('max_order') or 0) + 1
    ws_write(FACET_VALUES_TABLE, [{
        'facet_name': facet_name,
        'enum_value': enum_value,
        'display_label': display_label,
        'sort_order': sort_order,
        'is_active': True,
        'created_at': now,
        'updated_at': now,
    }])


def deactivate_enum_value(facet_name, enum_value):
    logger.info(f'Deactivating enum value {facet_name}.{enum_value}')
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    ws_execute(
        f"UPDATE {FACET_VALUES_TABLE} SET is_active = false, updated_at = '{now}' WHERE facet_name = '{facet_name}' AND enum_value = '{enum_value}'"
    )


def main():
    logger.info('Building facet enum service contract')
    ensure_tables()
    seed_facet_enums()
    seed_facet_values()
    verify_schema()
    all_facets = get_all_facets()
    logger.info(f'Facet enum service contract complete. Loaded {len(all_facets)} facets.')
    for facet in all_facets:
        values = get_facet_values(facet['facet_name'])
        logger.info(f"  {facet['facet_name']}: {len(values)} values")


if __name__ == '__main__':
    import requests
    main()
    sys.exit(0)
import sys
import requests
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log')]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'facet_enum_service_contract'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
FACET_ENUM_TABLE = 'facet_enums'
FACET_VALUES_TABLE = 'facet_values'

REQUIRED_FACET_COLUMNS = [
    'facet_name',
    'description',
    'created_at',
    'updated_at',
]

REQUIRED_VALUE_COLUMNS = [
    'id',
    'facet_name',
    'enum_value',
    'display_label',
    'sort_order',
    'is_active',
    'created_at',
    'updated_at',
]

FACET_DEFINITIONS = {
    'verdict': [
        ('TRUSTED', 'Trusted', 1),
        ('AMBER', 'Amber - Use Caution', 2),
        ('UNTRUSTED', 'Untrusted', 3),
        ('UNKNOWN', 'Unknown', 4),
        ('KNOWN_THREAT', 'Known Threat', 5),
        ('HIGH_RISK_ISOLATED', 'High Risk Isolated', 6),
        ('CAUTION_LIMITED', 'Caution Limited', 7),
        ('TRUSTED_RESEARCH', 'Trusted Research', 8),
        ('ENTERPRISE_CONTROLLED', 'Enterprise Controlled', 9),
    ],
    'risk_tier': [
        ('TIER_1_CRITICAL', 'Tier 1 Critical', 1),
        ('TIER_2_HIGH', 'Tier 2 High', 2),
        ('TIER_3_ELEVATED', 'Tier 3 Elevated', 3),
        ('TIER_4_MODERATE', 'Tier 4 Moderate', 4),
        ('TIER_5_LOW', 'Tier 5 Low', 5),
        ('TIER_6_MINIMAL', 'Tier 6 Minimal', 6),
    ],
    'signal_type': [
        ('supply_chain', 'Supply Chain', 1),
        ('community_signal', 'Community Signal', 2),
        ('temporal_stability', 'Temporal Stability', 3),
        ('permission_scope', 'Permission Scope', 4),
        ('tool_description', 'Tool Description', 5),
        ('injection_resilience', 'Injection Resilience', 6),
        ('context_efficiency', 'Context Efficiency', 7),
        ('domain_trust', 'Domain Trust', 8),
        ('registry_breadth', 'Registry Breadth', 9),
        ('evidence_density', 'Evidence Density', 10),
        ('vendor_concentration', 'Vendor Concentration', 11),
        ('traffic_fingerprint', 'Traffic Fingerprint', 12),
    ],
    'registry_source': [
        ('npm', 'NPM Registry', 1),
        ('github', 'GitHub', 2),
        ('smithery', 'Smithery AI', 3),
        ('toolbench', 'Arcade Toolbench', 4),
        ('manual', 'Manual Submission', 5),
        ('directory', 'MCP Directory', 6),
    ],
    'attestation_status': [
        ('none', 'No Attestation', 1),
        ('pending', 'Pending', 2),
        ('verified', 'Verified', 3),
        ('expired', 'Expired', 4),
        ('revoked', 'Revoked', 5),
    ],
    'submission_source': [
        ('user', 'User Submission', 1),
        ('auto_discovery', 'Auto Discovery', 2),
        ('api', 'API Submission', 3),
        ('bulk_import', 'Bulk Import', 4),
    ],
    'threat_severity': [
        ('critical', 'Critical', 1),
        ('high', 'High', 2),
        ('medium', 'Medium', 3),
        ('low', 'Low', 4),
        ('info', 'Informational', 5),
    ],
    'scan_status': [
        ('pending', 'Pending', 1),
        ('in_progress', 'In Progress', 2),
        ('completed', 'Completed', 3),
        ('failed', 'Failed', 4),
        ('timeout', 'Timeout', 5),
    ],
    'approval_status': [
        ('pending', 'Pending Review', 1),
        ('approved', 'Approved', 2),
        ('rejected', 'Rejected', 3),
        ('escalated', 'Escalated', 4),
        ('exempted', 'Exempted', 5),
    ],
    'compliance_status': [
        ('compliant', 'Compliant', 1),
        ('non_compliant', 'Non Compliant', 2),
        ('pending_review', 'Pending Review', 3),
        ('not_applicable', 'Not Applicable', 4),
    ],
}


def ws_query(sql):
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_tables():
    logger.info('Creating facet_enums table')
    ws_execute(f'''
        CREATE TABLE IF NOT EXISTS {FACET_ENUM_TABLE} (
            facet_name VARCHAR PRIMARY KEY,
            description TEXT,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
    ''')
    logger.info('Creating facet_values table')
    ws_execute(f'''
        CREATE TABLE IF NOT EXISTS {FACET_VALUES_TABLE} (
            id INTEGER PRIMARY KEY,
            facet_name VARCHAR NOT NULL,
            enum_value VARCHAR NOT NULL,
            display_label VARCHAR NOT NULL,
            sort_order INTEGER,
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            UNIQUE(facet_name, enum_value)
        )
    ''')


def verify_tables():
    logger.info('Verifying table schemas')
    facet_cols = ws_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'facet_enums'"
    )
    facet_col_names = [r['column_name'] for r in facet_cols]
    for col in REQUIRED_FACET_COLUMNS:
        if col not in facet_col_names:
            raise ValueError(f'Missing required column in facet_enums: {col}')

    value_cols = ws_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'facet_values'"
    )
    value_col_names = [r['column_name'] for r in value_cols]
    for col in REQUIRED_VALUE_COLUMNS:
        if col not in value_col_names:
            raise ValueError(f'Missing required column in facet_values: {col}')
    logger.info('Table schemas verified successfully')


def seed_facet_definitions():
    logger.info('Seeding facet definitions')
    now = utc_now_iso()
    for facet_name, values in FACET_DEFINITIONS.items():
        existing = ws_query(
            f"SELECT 1 FROM {FACET_ENUM_TABLE} WHERE facet_name = '{facet_name}' LIMIT 1"
        )
        if not existing:
            ws_write(FACET_ENUM_TABLE, [{
                'facet_name': facet_name,
                'description': f'Enumeration values for {facet_name}',
                'created_at': now,
                'updated_at': now,
            }])
            logger.info(f'Seeded facet: {facet_name}')

        for enum_value, display_label, sort_order in values:
            existing_val = ws_query(
                f"SELECT 1 FROM {FACET_VALUES_TABLE} WHERE facet_name = '{facet_name}' AND enum_value = '{enum_value}' LIMIT 1"
            )
            if not existing_val:
                ws_write(FACET_VALUES_TABLE, [{
                    'id': sort_order,
                    'facet_name': facet_name,
                    'enum_value': enum_value,
                    'display_label': display_label,
                    'sort_order': sort_order,
                    'is_active': True,
                    'created_at': now,
                    'updated_at': now,
                }])
                logger.info(f'  Seeded value: {facet_name}.{enum_value}')


def get_facet_values(facet_name):
    return ws_query(
        f"SELECT * FROM {FACET_VALUES_TABLE} WHERE facet_name = '{facet_name}' AND is_active = true ORDER BY sort_order"
    )


def get_all_facets():
    return ws_query(f'SELECT * FROM {FACET_ENUM_TABLE} ORDER BY facet_name')


def check_facet_exists(facet_name):
    rows = ws_query(
        f"SELECT 1 FROM {FACET_ENUM_TABLE} WHERE facet_name = '{facet_name}' LIMIT 1"
    )
    return bool(rows)


def check_value_exists(facet_name, enum_value):
    rows = ws_query(
        f"SELECT 1 FROM {FACET_VALUES_TABLE} WHERE facet_name = '{facet_name}' AND enum_value = '{enum_value}' LIMIT 1"
    )
    return bool(rows)


def add_facet_value(facet_name, enum_value, display_label, sort_order=None):
    if not check_facet_exists(facet_name):
        raise ValueError(f'Facet does not exist: {facet_name}')
    now = utc_now_iso()
    if sort_order is None:
        max_order = ws_query(
            f'SELECT MAX(sort_order) as max_ord FROM {FACET_VALUES_TABLE} WHERE facet_name = \'{facet_name}\''
        )
        sort_order = (max_order[0]['max_ord'] or 0) + 1
    if not check_value_exists(facet_name, enum_value):
        ws_write(FACET_VALUES_TABLE, [{
            'id': sort_order,
            'facet_name': facet_name,
            'enum_value': enum_value,
            'display_label': display_label,
            'sort_order': sort_order,
            'is_active': True,
            'created_at': now,
            'updated_at': now,
        }])
        logger.info(f'Added value {facet_name}.{enum_value}')


def deactivate_facet_value(facet_name, enum_value):
    now = utc_now_iso()
    ws_execute(
        f"UPDATE {FACET_VALUES_TABLE} SET is_active = false, updated_at = '{now}' WHERE facet_name = '{facet_name}' AND enum_value = '{enum_value}'"
    )
    logger.info(f'Deactivated {facet_name}.{enum_value}')


def compute_deterministic_id(facet_name, enum_value):
    import hashlib
    raw = f'{facet_name}:{enum_value}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


def main():
    logger.info('Building facet enum service contract')
    create_tables()
    seed_facet_definitions()
    verify_tables()
    facets = get_all_facets()
    logger.info(f'Facet enum contract complete: {len(facets)} facets loaded')
    for facet in facets:
        values = get_facet_values(facet['facet_name'])
        logger.info(f"  {facet['facet_name']}: {len(values)} active values")
    sys.exit(0)


if __name__ == '__main__':
    main()