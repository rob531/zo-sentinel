"""zo_sentinel package - Auto-emitted service package with staged->active promotion support."""

from typing import Optional, Any
import os

# Core base classes for service models
class PerspectiveSnapshot:
    """Base class for perspective snapshot models."""
    
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self._id: Optional[int] = kwargs.get('id')
        self.created_at = kwargs.get('created_at')
        self.updated_at = kwargs.get('updated_at')
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}


class TargetServer:
    """Base class for target server attestation models."""
    
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.server_id: Optional[str] = kwargs.get('server_id')
        self.status = kwargs.get('status', 'unknown')
        self.last_heartbeat = kwargs.get('last_heartbeat')
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}


class SentinelDirective:
    """Represents an auto-emitted sentinel directive."""
    
    def __init__(self, directive_id: str, directive_type: str, payload: dict):
        self.directive_id = directive_id
        self.directive_type = directive_type
        self.payload = payload
        self.status = 'pending'
    
    def to_dict(self) -> dict:
        return {
            'directive_id': self.directive_id,
            'directive_type': self.directive_type,
            'payload': self.payload,
            'status': self.status
        }


# Service state constants
class ServiceState:
    STAGED = 'staged'
    ACTIVE = 'active'
    DEPRECATED = 'deprecated'


# Exception hierarchy
class SentinelError(Exception):
    """Base exception for sentinel operations."""
    pass


class DirectiveError(SentinelError):
    """Exception for directive processing errors."""
    pass


class AttestationError(SentinelError):
    """Exception for attestation failures."""
    pass


class MeshStoreError(SentinelError):
    """Exception for mesh store communication errors."""
    pass


# MESH/pipeline interaction helpers
ZOCOMPUTER_URL = os.environ.get('ZOCOMPUTER_URL', 'http://127.0.0.1:8772')


def query_mesh_store(query: dict, service_token: Optional[str] = None) -> dict:
    """Query the ZoComputer mesh store for pipeline data."""
    import requests
    headers = {'Content-Type': 'application/json'}
    if service_token:
        headers['Authorization'] = f'Bearer {service_token}'
    try:
        response = requests.post(
            f'{ZOCOMPUTER_URL}/query',
            json=query,
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise MeshStoreError(f'Mesh store query failed: {e}')


def write_mesh_signal(signal: dict, service_token: Optional[str] = None) -> dict:
    """Write a signal to the mesh store."""
    import requests
    headers = {'Content-Type': 'application/json'}
    if service_token:
        headers['Authorization'] = f'Bearer {service_token}'
    try:
        response = requests.post(
            f'{ZOCOMPUTER_URL}/signal',
            json=signal,
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise MeshStoreError(f'Mesh signal write failed: {e}')


# Version info
__version__ = '1.0.0'


if __name__ == '__main__':
    from fastapi import FastAPI, Depends
    from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String
    from sqlalchemy.orm import sessionmaker, Session
    from app.db import get_session
    
    app = FastAPI()
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    metadata = MetaData()
    Table('test_table', metadata, Column('id', Integer, primary_key=True), Column('name', String))
    metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    that_app = app
    that_app.dependency_overrides[get_session] = override_get_session
    
    from app.models import PerspectiveSnapshot as ImportedPS
    from verify_attestation_generation import TargetServer as ImportedTS
    
    import materialize_canonical_family
    import ops_audit_state
    import shadow_decision
    import state_loopback
    import vast_jobs
    
    print('PASS')