import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import urlencode

import requests
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ask_corpus_index, service_health, perspective_events

DRIFT_WINDOW_DAYS = 7
DRIFT_THRESHOLD = 0.3
ANOMALY_MIN_SIZE_CHANGE_PCT = 50
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"

def get_ask_corpus_index(session: Session, server_id: str, days: int) -> List[Dict]:
    """Get corpus index entries for a server within the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    return session.query(ask_corpus_index).filter(
        ask_corpus_index.server_id == server_id,
        ask_corpus_index.indexed_at >= cutoff
    ).all()

def compute_drift_score(session: Session, server_id: str) -> Dict:
    """Compute drift score for a server's corpus."""
    # Get current and previous window data
    current_window = get_ask_corpus_index(session, server_id, DRIFT_WINDOW_DAYS // 2)
    previous_window = get_ask_corpus_index(session, server_id, DRIFT_WINDOW_DAYS)

    if not previous_window:
        return {
            'server_id': server_id,
            'drift_score': 0.0,
            'delta_terms': 0,
            'delta_size': 0,
            'anomaly_flag': False
        }

    # Compute term delta
    current_terms = set()
    for entry in current_window:
        current_terms.update(entry.terms)

    previous_terms = set()
    for entry in previous_window:
        previous_terms.update(entry.terms)

    delta_terms = len(current_terms.symmetric_difference(previous_terms))

    # Compute size delta
    current_size = len(current_window)
    previous_size = len(previous_window)
    delta_size = current_size - previous_size

    # Compute drift score
    term_score = min(delta_terms / max(len(current_terms), len(previous_terms)), 1.0) if max(len(current_terms), len(previous_terms)) > 0 else 0.0
    size_score = abs(delta_size) / max(current_size, previous_size) if max(current_size, previous_size) > 0 else 0.0
    drift_score = (term_score + size_score) / 2

    # Check for anomalies
    anomaly_flag = drift_score > DRIFT_THRESHOLD or abs(delta_size) / previous_size > ANOMALY_MIN_SIZE_CHANGE_PCT / 100

    return {
        'server_id': server_id,
        'drift_score': drift_score,
        'delta_terms': delta_terms,
        'delta_size': delta_size,
        'anomaly_flag': anomaly_flag
    }

def check_all_corpus_drift(session: Session) -> List[Dict]:
    """Scan all servers in ask_corpus_index, compute drift per server."""
    # Get all server_ids
    server_ids = session.query(ask_corpus_index.server_id).distinct().all()
    server_ids = [sid[0] for sid in server_ids]

    results = []
    for server_id in server_ids:
        result = compute_drift_score(session, server_id)
        results.append(result)

        # Log anomaly to perspective_events if detected
        if result['anomaly_flag']:
            event = perspective_events(
                perspective_id='drift_guard',
                change_type='corpus_drift',
                server_id=server_id,
                drift_score=result['drift_score'],
                delta_terms=result['delta_terms'],
                delta_size=result['delta_size'],
                detected_at=datetime.utcnow()
            )
            session.add(event)

    return results

def heartbeat(session: Session):
    """Record heartbeat in service_health."""
    session.add(service_health(
        service_name='ask_corpus_drift_guard',
        last_check=datetime.utcnow(),
        status='running'
    ))
    session.commit()

def run():
    """Main entry point. Runs loop: scan corpus, compute drift scores, alert."""
    session = get_session()
    try:
        while True:
            # Check drift
            results = check_all_corpus_drift(session)

            # Print results
            for result in results:
                print(json.dumps(result))

            # Heartbeat
            heartbeat(session)

            # Sleep for 60s
            time.sleep(60)
    finally:
        session.close()

if __name__ == '__main__':
    # Mock ask_corpus_index rows for self-test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override get_session for testing
    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Insert test data
    session = SessionLocal()
    session.add_all([
        ask_corpus_index(
            server_id='srv1',
            snippet='test snippet',
            terms=['a', 'b'],
            content_hash='abc123',
            indexed_at=datetime.strptime('2026-07-01T00:00:00Z', '%Y-%m-%dT%H:%M:%SZ')
        ),
        ask_corpus_index(
            server_id='srv1',
            snippet='different',
            terms=['x', 'y', 'z'],
            content_hash='def456',
            indexed_at=datetime.strptime('2026-07-05T00:00:00Z', '%Y-%m-%dT%H:%M:%SZ')
        ),
        ask_corpus_index(
            server_id='srv2',
            snippet='stable',
            terms=['a'],
            content_hash='xyz789',
            indexed_at=datetime.strptime('2026-07-01T00:00:00Z', '%Y-%m-%dT%H:%M:%SZ')
        ),
    ])
    session.commit()

    # Test compute_drift_score
    result = compute_drift_score(session, 'srv1')
    assert result['server_id'] == 'srv1'
    assert result['anomaly_flag'] is True

    print('PASS: ask_corpus_drift_guard')

    session.close()