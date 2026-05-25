#!/usr/bin/env python3
"""Create auth_tokens table for email GUID auth."""
import requests
r = requests.post('http://127.0.0.1:8772/execute', json={
    'sql': '''CREATE TABLE IF NOT EXISTS auth_tokens (
        token_id     VARCHAR PRIMARY KEY,
        action       VARCHAR,
        mcp_name     VARCHAR,
        submission_id VARCHAR,
        requested_by VARCHAR,
        admin_email  VARCHAR,
        expires_at   TIMESTAMPTZ,
        used         BOOLEAN DEFAULT FALSE,
        used_at      TIMESTAMPTZ,
        created_at   TIMESTAMPTZ DEFAULT now()
    )''',
    'wait': True
}, timeout=10)
print('[OK] auth_tokens' if r.status_code == 200 else f'[!!] {r.status_code} {r.text}')