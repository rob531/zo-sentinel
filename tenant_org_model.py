import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
WRITE_TIMEOUT = 30

_COUNTER = {"org": 0, "user": 0, "api_key": 0}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post_write(table: str, rows: Dict[str, Any]) -> Dict[str, Any]:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post_query(sql: str) -> Dict[str, Any]:
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json={"sql": sql}, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post_execute(sql: str) -> None:
    resp = requests.post(f"{WRITE_SERVICE_URL}/execute", json={"sql": sql}, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()


def _deterministic_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def ensure_orgs_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS orgs (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(255) NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL
    )
    """
    try:
        _post_execute(sql)
    except Exception:
        pass


def ensure_users_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS org_users (
        id VARCHAR(64) PRIMARY KEY,
        org_id VARCHAR(64) NOT NULL,
        email VARCHAR(255) NOT NULL,
        role VARCHAR(32) NOT NULL DEFAULT 'member',
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(org_id, email)
    )
    """
    try:
        _post_execute(sql)
    except Exception:
        pass


def ensure_api_keys_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS org_api_keys (
        id VARCHAR(64) PRIMARY KEY,
        org_id VARCHAR(64) NOT NULL,
        key_hash VARCHAR(255) NOT NULL,
        label VARCHAR(128) NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL
    )
    """
    try:
        _post_execute(sql)
    except Exception:
        pass


def init_all_tables() -> None:
    ensure_orgs_table()
    ensure_users_table()
    ensure_api_keys_table()


def create_org(name: str) -> str:
    org_id = _deterministic_id("org", name, _utc_now_iso())
    created_at = _utc_now_iso()
    try:
        _post_write(
            "orgs",
            {"id": org_id, "name": name, "created_at": created_at},
        )
    except Exception:
        _COUNTER["org"] += 1
    return org_id


def add_member(org_id: str, email: str, role: str = "member") -> str:
    user_id = _deterministic_id("usr", org_id, email, _utc_now_iso())
    created_at = _utc_now_iso()
    try:
        _post_write(
            "org_users",
            {"id": user_id, "org_id": org_id, "email": email, "role": role, "created_at": created_at},
        )
    except Exception:
        _COUNTER["user"] += 1
    return user_id


def create_api_key(org_id: str, label: str = "") -> tuple[str, str]:
    raw_key = f"sk_{secrets.token_hex(32)}"
    key_hash = hmac.new(b"", raw_key.encode(), hashlib.sha256).hexdigest()
    key_id = _deterministic_id("key", org_id, raw_key, _utc_now_iso())
    created_at = _utc_now_iso()
    try:
        _post_write(
            "org_api_keys",
            {"id": key_id, "org_id": org_id, "key_hash": key_hash, "label": label, "created_at": created_at},
        )
    except Exception:
        _COUNTER["api_key"] += 1
    return key_id, raw_key


def scope_by_org_id(base_sql: str, org_id: str) -> str:
    if "WHERE" in base_sql.upper():
        return f"{base_sql.rstrip().rstrip(';')} AND org_id = '{org_id}'"
    where = f" WHERE org_id = '{org_id}'"
    if "GROUP BY" in base_sql.upper():
        return base_sql.replace("GROUP BY", f"{where} GROUP BY", 1)
    return f"{base_sql.rstrip().rstrip(';')}{where}"


def list_members(org_id: str) -> List[Dict[str, Any]]:
    sql = f"SELECT id, org_id, email, role, created_at FROM org_users WHERE org_id = '{org_id}'"
    try:
        result = _post_query(sql)
        return result.get("rows", [])
    except Exception:
        return []


def list_api_keys(org_id: str) -> List[Dict[str, Any]]:
    sql = f"SELECT id, org_id, label, created_at FROM org_api_keys WHERE org_id = '{org_id}'"
    try:
        result = _post_query(sql)
        return result.get("rows", [])
    except Exception:
        return []


def revoke_api_key(key_id: str) -> None:
    sql = f"DELETE FROM org_api_keys WHERE id = '{key_id}'"
    try:
        _post_execute(sql)
    except Exception:
        pass


def remove_member(user_id: str) -> None:
    sql = f"DELETE FROM org_users WHERE id = '{user_id}'"
    try:
        _post_execute(sql)
    except Exception:
        pass


def verify_api_key(raw_key: str) -> Optional[str]:
    key_hash = hmac.new(b"", raw_key.encode(), hashlib.sha256).hexdigest()
    sql = f"SELECT org_id FROM org_api_keys WHERE key_hash = '{key_hash}'"
    try:
        result = _post_query(sql)
        rows = result.get("rows", [])
        if rows:
            return rows[0]["org_id"]
    except Exception:
        pass
    return None


def run() -> None:
    init_all_tables()
    print("tenant_org_model: tables initialised")


if __name__ == "__main__":
    run()