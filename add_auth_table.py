#!/usr/bin/env python3
"""Add admin_auth_tokens table for GUID email auth."""
import requests

EX = 'http://127.0.0.1:8772/execute'

sql = """CREATE TABLE IF NOT EXISTS admin_auth_tokens (
    token       VARCHAR PRIMARY KEY,
    email       VARCHAR NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used        BOOLEAN DEFAULT FALSE,
    used_at     TIMESTAMPTZ,
    purpose     VARCHAR DEFAULT 'admin_access'
)"""

r = requests.post(EX, json={'sql': sql, 'wait': True}, timeout=10)
print('[OK] admin_auth_tokens' if r.status_code == 200 else f'[!!] HTTP {r.status_code}: {r.text}')