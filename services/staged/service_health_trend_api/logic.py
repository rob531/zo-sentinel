from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import Column, String, DateTime, Integer, Table, MetaData
from sqlalchemy.orm import Session
from app.db import get_session
import write_service


class ServiceTrendResponse(BaseModel):
    window_hours: int
    services: List[Dict[str, Any]]


def get_service_health_trend(
    window_hours: int = 24,
    session: Optional[Session] = None
) -> ServiceTrendResponse:
    """
    Read service_health rows ordered by last_heartbeat desc,
    compute per-service state transitions (ok->stale->missing) over rolling window,
    track time_in_state for each daemon,
    identify daemons that have gone stale N times.
    """
    window_start = datetime.utcnow() - timedelta(hours=window_hours)
    
    query = f"""
        SELECT name, current_status, last_heartbeat, previous_status,
               time_in_state_seconds, stale_count
        FROM service_health
        WHERE last_heartbeat >= '{window_start.isoformat()}'
        ORDER BY last_heartbeat DESC
    """
    
    response = write_service.post_query(
        url="http://127.0.0.1:8772/query",
        query=query
    )
    
    services_data = response.get('results', [])
    
    services_map: Dict[str, Dict[str, Any]] = {}
    
    for row in services_data:
        name = row['name']
        if name not in services_map:
            services_map[name] = {
                'name': name,
                'current_status': row['current_status'],
                'last_heartbeat': row['last_heartbeat'],
                'transitions': [],
                'time_in_state': 0,
                'stale_count': 0
            }
        
        if row.get('previous_status'):
            services_map[name]['transitions'].append({
                'from': row['previous_status'],
                'to': row['current_status'],
                'at': row['last_heartbeat']
            })
        
        services_map[name]['time_in_state'] += row.get('time_in_state_seconds', 0)
        
        if row['current_status'] == 'stale':
            services_map[name]['stale_count'] += row.get('stale_count', 0)
    
    return ServiceTrendResponse(
        window_hours=window_hours,
        services=list(services_map.values())
    )


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    
    metadata = MetaData()
    service_health_table = Table(
        'service_health',
        metadata,
        Column('id', Integer, primary_key=True),
        Column('name', String(255)),
        Column('current_status', String(50)),
        Column('last_heartbeat', DateTime),
        Column('previous_status', String(50), nullable=True),
        Column('time_in_state_seconds', Integer),
        Column('stale_count', Integer),
    )
    
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    base_time = datetime.utcnow()
    
    with TestingSessionLocal() as db:
        db.add(service_health_table.insert().values(
            name='daemon-1',
            current_status='ok',
            last_heartbeat=base_time - timedelta(minutes=5),
            previous_status='stale',
            time_in_state_seconds=300,
            stale_count=1,
        ))
        db.add(service_health_table.insert().values(
            name='daemon-2',
            current_status='missing',
            last_heartbeat=base_time - timedelta(minutes=20),
            previous_status='ok',
            time_in_state_seconds=1200,
            stale_count=1,
        ))
        db.add(service_health_table.insert().values(
            name='daemon-3',
            current_status='missing',
            last_heartbeat=base_time - timedelta(hours=1, minutes=10),
            previous_status='stale',
            time_in_state_seconds=600,
            stale_count=1,
        ))
        db.add(service_health_table.insert().values(
            name='daemon-4',
            current_status='ok',
            last_heartbeat=base_time - timedelta(hours=1, minutes=30),
            previous_status='missing',
            time_in_state_seconds=900,
            stale_count=0,
        ))
        db.add(service_health_table.insert().values(
            name='daemon-5',
            current_status='stale',
            last_heartbeat=base_time - timedelta(hours=1, minutes=50),
            previous_status=None,
            time_in_state_seconds=6600,
            stale_count=1,
        ))
        db.commit()
    
    def override_get_session():
        return TestingSessionLocal()
    
    the_app = FastAPI()
    
    @the_app.get("/api/service/health/trend")
    def endpoint(window_hours: int = 24, session: Session = Depends(get_session)):
        return get_service_health_trend(window_hours, session)
    
    the_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(the_app)
    response = client.get("/api/service/health/trend?window_hours=2")
    assert response.status_code == 200
    
    data = response.json()
    assert len(data['services']) == 5
    
    services_by_name = {s['name']: s for s in data['services']}
    
    assert services_by_name['daemon-1']['current_status'] == 'ok'
    assert services_by_name['daemon-1']['stale_count'] == 1
    assert services_by_name['daemon-2']['current_status'] == 'missing'
    assert services_by_name['daemon-2']['stale_count'] == 1
    assert services_by_name['daemon-3']['current_status'] == 'missing'
    assert services_by_name['daemon-3']['stale_count'] == 1
    assert services_by_name['daemon-4']['current_status'] == 'ok'
    assert services_by_name['daemon-4']['stale_count'] == 0
    assert services_by_name['daemon-5']['current_status'] == 'stale'
    assert services_by_name['daemon-5']['stale_count'] == 1
    
    print("PASS")