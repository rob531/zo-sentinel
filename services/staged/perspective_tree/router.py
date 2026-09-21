# services/staged/perspective_tree/logic.py
from datetime import datetime
from typing import List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Perspective, PerspectiveSnapshot, PerspectiveEvent, McpServerRegistry
from .schemas import PerspectiveTreeResponse, ServerInfo


def get_perspective_tree(session: Session, perspective_id: int) -> PerspectiveTreeResponse:
    perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise ValueError(f"Perspective {perspective_id} not found")

    root_servers: List[ServerInfo] = []
    
    snapshots = session.query(PerspectiveSnapshot).filter(
        PerspectiveSnapshot.perspective_id == perspective_id
    ).all()
    
    membership_server_ids = set()
    for snap in snapshots:
        if snap.membership:
            import json
            try:
                members = json.loads(snap.membership) if isinstance(snap.membership, str) else snap.membership
                if isinstance(members, list):
                    for m in members:
                        if isinstance(m, dict):
                            membership_server_ids.add(m.get('server_id'))
                        else:
                            membership_server_ids.add(str(m))
                elif isinstance(members, dict):
                    for k in members.keys():
                        membership_server_ids.add(k)
            except (json.JSONDecodeError, TypeError):
                pass

    if not membership_server_ids:
        return PerspectiveTreeResponse(
            perspective_id=perspective_id,
            perspective_name=perspective.name,
            root_servers=[],
            total_servers=0
        )

    server_info_map = {}
    servers = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id.in_(membership_server_ids)
    ).all()
    for s in servers:
        server_info_map[s.server_id] = s

    event_counts = dict(
        session.query(PerspectiveEvent.server_id, func.count(PerspectiveEvent.id))
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .group_by(PerspectiveEvent.server_id)
        .all()
    )

    last_change = dict(
        session.query(
            PerspectiveEvent.server_id,
            PerspectiveEvent.change_type
        )
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .distinct(PerspectiveEvent.server_id)
        .all()
    )

    for server_id in membership_server_ids:
        server = server_info_map.get(server_id)
        if server:
            root_servers.append(ServerInfo(
                server_id=server.server_id,
                name=server.name or "unknown",
                risk_tier=server.risk_tier,
                last_seen=server.last_seen,
                event_count=event_counts.get(server_id, 0),
                last_change_type=last_change.get(server_id)
            ))

    return PerspectiveTreeResponse(
        perspective_id=perspective_id,
        perspective_name=perspective.name,
        root_servers=root_servers,
        total_servers=len(root_servers)
    )