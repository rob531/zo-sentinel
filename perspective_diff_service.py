from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, PerspectiveSnapshot
from app.services.write_service import write_service
from app.services.notification_service import queue_notification
import requests

def snapshot_perspective(id: int, session: Session = Depends(get_session)):
    servers = session.query(MCPServerRegistry).filter(MCPServerRegistry.org_id == id).all()
    snapshot_data = {server.server_id: server.risk_tier for server in servers}
    write_service('perspective_snapshots', {'id': id, 'data': snapshot_data})

def diff_perspective(id: int, session: Session = Depends(get_session)):
    response = requests.post('http://127.0.0.1:8772/query', json={'table': 'perspective_snapshots', 'id': id})
    snapshot_data = response.json()['data']

    current_servers = session.query(MCPServerRegistry).filter(MCPServerRegistry.org_id == id).all()
    current_data = {server.server_id: server.risk_tier for server in current_servers}

    entered = [server_id for server_id in current_data if server_id not in snapshot_data]
    left = [server_id for server_id in snapshot_data if server_id not in current_data]
    tier_changed = [{'server_id': server_id, 'old': snapshot_data[server_id], 'new': current_data[server_id]}
                    for server_id in current_data if server_id in snapshot_data and current_data[server_id] != snapshot_data[server_id]]

    for server_id in entered:
        queue_notification(f'Server {server_id} entered the perspective')
    for server_id in left:
        queue_notification(f'Server {server_id} left the perspective')
    for change in tier_changed:
        queue_notification(f'Server {change["server_id"]} changed risk tier from {change["old"]} to {change["new"]}')

    return {'entered': entered, 'left': left, 'tier_changed': tier_changed}

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    session.add_all([
        MCPServerRegistry(server_id=1, org_id=1, risk_tier='low'),
        MCPServerRegistry(server_id=2, org_id=1, risk_tier='medium'),
        MCPServerRegistry(server_id=3, org_id=1, risk_tier='high')
    ])
    session.commit()

    snapshot_perspective(1, session)

    session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == 2).update({'risk_tier': 'high'})
    session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == 3).delete()
    session.commit()

    diff_result = diff_perspective(1, session)

    assert diff_result == {'entered': [], 'left': [3], 'tier_changed': [{'server_id': 2, 'old': 'medium', 'new': 'high'}]}

    print('PASS')