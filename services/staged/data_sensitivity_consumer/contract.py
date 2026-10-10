from datetime import datetime, timezone
from typing import List, Any
from fastapi import FastAPI, Depends, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, Table, Column, String, Float, DateTime, MetaData
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import requests
import uvicorn
import threading
import time

from app.db import get_session
from app.models import McpLlmAxisScore

router = None
app = FastAPI()

def _create_router():
    global router
    from fastapi import APIRouter
    router = APIRouter(prefix="/data_sensitivity", tags=["data_sensitivity"])

    @router.get("/health")
    def health():
        return {"status": "healthy", "service": "data_sensitivity_consumer"}

    @router.post("/process")
    def process(response: Response):
        response.status_code = 202
        threading.Thread(target=_run_continuous_processing, daemon=True).start()
        return {"status": "processing_started"}

    app.include_router(router)

def _run_continuous_processing():
    last_heartbeat = 0
    while True:
        _process_batch()
        now = time.time()
        if now - last_heartbeat >= 60:
            _send_heartbeat()
            last_heartbeat = now
        time.sleep(5)

def _process_batch():
    from app.db import get_session
    session_gen = get_session()
    session = next(session_gen)
    try:
        scores = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.axis_name == 'data_sensitivity'
        ).order_by(McpLlmAxisScore.scored_at.desc()).limit(100).all()
        for score in scores:
            tier = _derive_risk_tier(score.p_top)
            _write_to_risk_register(score.server_id, tier, score.p_top, score.scored_at)
    finally:
        try:
            next(session_gen)
        except StopIteration:
            pass

def _derive_risk_tier(p_top: float) -> str:
    if p_top >= 0.95:
        return 'TRUSTED_GENERAL'
    elif p_top >= 0.90:
        return 'TRUSTED_RESEARCH'
    elif p_top >= 0.80:
        return 'ENTERPRISE_CONTROLLED'
    elif p_top >= 0.70:
        return 'CAUTION_LIMITED'
    elif p_top >= 0.50:
        return 'HIGH_RISK_ISOLATED'
    else:
        return 'KNOWN_THREAT'

def _write_to_risk_register(server_id: str, risk_tier: str, p_top: float, scored_at: datetime):
    payload = {
        'server_id': server_id,
        'risk_tier': risk_tier,
        'axis_name': 'data_sensitivity',
        'p_top': p_top,
        'scored_at': scored_at.isoformat() if scored_at else datetime.now(timezone.utc).isoformat(),
    }
    try:
        requests.post('http://127.0.0.1:8772/mcp_risk_register', json=payload, timeout=5)
    except Exception:
        pass

def _send_heartbeat():
    payload = {
        'server_id': 'data_sensitivity_consumer',
        'status': 'healthy',
        'port': 8784,
        'service': 'data_sensitivity_consumer',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    try:
        requests.post('http://127.0.0.1:8772/service_health', json=payload, timeout=5)
    except Exception:
        pass

def run():
    _create_router()
    uvicorn.run(app, host='127.0.0.1', port=8784, log_level='warning')

if __name__ == '__main__':
    run()


def contract_test():
    engine = create_engine('sqlite:///:memory:', poolclass=StaticPool, connect_args={'check_same_thread': False})
    metadata = MetaData()
    Table('mcp_llm_axis_scores', metadata,
          Column('id', String),
          Column('server_id', String),
          Column('axis_name', String),
          Column('p_top', Float),
          Column('p_critical', Float),
          Column('p_danger', Float),
          Column('label_index', Float),
          Column('scored_at', DateTime),
          Column('label', String),
          Column('adapter_sha256', String),
          Column('model_version', String),
          Column('decision_rule_version', String),
          Column('escalated', String),
          Column('escalated_to', String),
          Column('probs', String))
    Table('mcp_risk_register', metadata,
          Column('id', String),
          Column('server_id', String),
          Column('risk_tier', String),
          Column('axis_name', String),
          Column('p_top', Float),
          Column('scored_at', DateTime),
          Column('created_at', DateTime))
    metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()
    now = datetime.now(timezone.utc)
    test_data = [
        ('srv1', 'data_sensitivity', 0.97, now),
        ('srv2', 'data_sensitivity', 0.75, now),
        ('srv3', 'data_sensitivity', 0.60, now),
        ('srv4', 'data_sensitivity', 0.85, now),
        ('srv5', 'data_sensitivity', 0.30, now),
    ]
    for srv_id, axis, p_top, scored in test_data:
        test_session.execute(text(
            "INSERT INTO mcp_llm_axis_scores (id, server_id, axis_name, p_top, scored_at) VALUES (:id, :srv, :axis, :p, :scored)"
        ), {'id': f'ax-{srv_id}', 'srv': srv_id, 'axis': axis, 'p': p_top, 'scored': scored})
    test_session.commit()
    _create_router()
    write_outputs = []
    def mock_write(payload):
        write_outputs.append(payload)
    original_write = _write_to_risk_register
    import services.staged.data_sensitivity_consumer.contract as self_module
    self_module._write_to_risk_register = mock_write
    try:
        with TestClient(app) as client:
            response = client.post('/data_sensitivity/process', timeout=10)
            assert response.status_code == 202
        time.sleep(3)
        assert len(write_outputs) == 5
        tiers = {o['risk_tier'] for o in write_outputs}
        assert 'TRUSTED_GENERAL' in tiers
        assert 'KNOWN_THREAT' in tiers
        assert 'CAUTION_LIMITED' in tiers
        assert 'HIGH_RISK_ISOLATED' in tiers
        assert 'ENTERPRISE_CONTROLLED' in tiers
    finally:
        self_module._write_to_risk_register = original_write
    return write_outputs

if __name__ == '__main__':
    try:
        contract_test()
        print('PASS')
    except Exception as e:
        print(f'FAIL: {e}')