import requests
from typing import Dict, Any
import logging
from datetime import datetime
from pydantic import BaseModel

class ServiceHealth(BaseModel):
    service: str
    last_heartbeat: str

class TableCreationRequest(BaseModel):
    table_name: str
    columns: Dict[str, str]
    primary_key: str
    wait: bool

def create_table(table_creation_request: TableCreationRequest) -> Any:
    log.info(f"Creating table {table_creation_request.table_name}")
    response = requests.post(
        EXECUTE_URL,
        json=table_creation_request.__dict__,
        timeout=10,
    )
    return response.json()

class AuditLog(BaseModel):
    target_server_id: str
    server_id: str
    timestamp: datetime

def create_audit_log(audit_log: AuditLog) -> Any:
    log.info(f"Creating audit log for {audit_log.target_server_id} on server {audit_log.server_id}")
    response = requests.post(
        EXECUTE_URL,
        json=audit_log.__dict__,
        timeout=10,
    )
    return response.json()

def validate_config() -> Dict[str, bool]:
    write_service_ok = False
    tables_exist = False
    required_ports_open = True
    env_vars_set = True
    schema_version = 1

    # Validate write service endpoint
    try:
        response = requests.post(
            EXECUTE_URL + "/health",
            timeout=3,
        )
        if response.status_code == 200:
            log.info("Write service is up")
            write_service_ok = True
        else:
            log.warning("Write service is down")
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to validate write service: {e}")

    # Validate tables
    try:
        response = requests.post(
            EXECUTE_URL + "/information_schema",
            json={"show": "tables"},
            timeout=10,
        )
        if response.status_code == 200:
            log.info("Tables exist")
            tables_exist = True
        else:
            log.warning("Tables do not exist")
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to validate tables: {e}")

    # Validate ports
    try:
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not sock.connect(("127.0.0.1", 8780)):
            log.warning("Port 8780 is down")
        else:
            sock.close()
            required_ports_open = True

        if not sock.connect(("127.0.0.1", 8781)):
            log.warning("Port 8781 is down")
        else:
            sock.close()

        if not sock.connect(("127.0.0.1", 8782)):
            log.warning("Port 8782 is down")
        else:
            sock.close()
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to validate ports: {e}")

    # Validate environment variables
    try:
        import os

        if not os.environ["WRITE_SERVICE"]:
            log.warning("Environment variable WRITE_SERVICE is missing")
        else:
            env_vars_set = True

        required_ports_open = False
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to validate environment variables: {e}")

    return {
        "write_service_ok": write_service_ok,
        "tables_exist": tables_exist,
        "required_ports_open": required_ports_open,
        "env_vars_set": env_vars_set,
        "schema_version": schema_version,
    }

def build_config_validator() -> Dict[str, bool]:
    validation_result = validate_config()
    if not validation_result["write_service_ok"]:
        return {"error": "Write service is down"}
    if not validation_result["tables_exist"]:
        return {"error": "Tables do not exist"}
    if not validation_result["required_ports_open"]:
        return {"error": "Required ports are down"}
    if not validation_result["env_vars_set"]:
        return {"error": "Environment variables are missing"}

    log.info("All checks passed")
    return {
        "write_service_ok": validation_result["write_service_ok"],
        "tables_exist": validation_result["tables_exist"],
        "required_ports_open": validation_result["required_ports_open"],
        "env_vars_set": validation_result["env_vars_set"],
        "schema_version": validation_result["schema_version"],
    }

import logging
logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    build_config_validator()