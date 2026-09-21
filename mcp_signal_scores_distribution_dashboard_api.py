from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, List
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry
from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy import func

router = APIRouter()

class SignalScoresDistributionResponse(BaseModel):
    signal_type: str
    score_distribution: Dict[float, int]
    top_servers: List[str]

@router.get("/dashboard/signal-scores-distribution", response_model=List[SignalScoresDistributionResponse])
async def get_signal_scores_distribution(db: Session = Depends(get_session)):
    query = """
    SELECT
        signal_type,
        score,
        COUNT(*) as count,
        ARRAY_AGG(server_id) as server_ids
    FROM mcp_signal_scores
    GROUP BY signal_type, score
    ORDER BY signal_type, score
    """

    result = db.execute(sa.text(query))
    rows = result.fetchall()

    signal_data = {}

    for row in rows:
        signal_type = row.signal_type
        score = float(row.score)
        count = row.count
        server_ids = row.server_ids

        if signal_type not in signal_data:
            signal_data[signal_type] = {
                'score_distribution': {},
                'server_counts': {}
            }

        signal_data[signal_type]['score_distribution'][score] = count

        for server_id in server_ids:
            if server_id in signal_data[signal_type]['server_counts']:
                signal_data[signal_type]['server_counts'][server_id] += 1
            else:
                signal_data[signal_type]['server_counts'][server_id] = 1

    response = []

    for signal_type, data in signal_data.items():
        top_servers = sorted(
            data['server_counts'].items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        top_server_names = []
        for server_id, count in top_servers:
            server = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
            if server:
                top_server_names.append(server.name)

        response.append({
            'signal_type': signal_type,
            'score_distribution': data['score_distribution'],
            'top_servers': top_server_names
        })

    return response

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users

    app = FastAPI()
    app.include_router(router)

    Base.metadata.create_all(bind=engine)

    test_client = TestClient(app)

    from app.db import get_session
    # FU-369: `app.dependency_overrides` is not a module in this repo, so the import
    # that stood here raised ModuleNotFoundError the moment this block ran. The
    # override is defined locally instead, per the pattern in
    # services/active/cadence_job_sla_report/contract.py.
    from sqlalchemy import create_engine as _fu369_create_engine
    from sqlalchemy.orm import sessionmaker as _fu369_sessionmaker

    _FU369Session = _fu369_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=_fu369_create_engine("sqlite:///:memory:"),
    )


    def _fu369_session_override(session_factory=None):
        """Test session override covering every call shape used in this repo.

        Called with a sessionmaker it returns a dependency callable bound to that
        factory; called with nothing it returns a Session, which is what a FastAPI
        dependency override needs AND what `with ... as session:` needs, because
        Session implements the context-manager protocol itself.
        """
        if session_factory is not None:
            return lambda: session_factory()
        return _FU369Session()

    _fu369_session_override()

    test_data = [
        (1, 'server1', 'type1', 0.5),
        (2, 'server2', 'type1', 0.7),
        (3, 'server3', 'type1', 0.5),
        (4, 'server4', 'type2', 0.8),
        (5, 'server5', 'type2', 0.6),
        (6, 'server6', 'type3', 0.9),
        (7, 'server7', 'type3', 0.9),
        (8, 'server8', 'type4', 0.4),
        (9, 'server9', 'type4', 0.4),
        (10, 'server10', 'type5', 0.3),
        (11, 'server11', 'type5', 0.3),
        (12, 'server12', 'type6', 0.2),
        (13, 'server13', 'type6', 0.2),
        (14, 'server14', 'type7', 0.1),
        (15, 'server15', 'type7', 0.1),
        (16, 'server16', 'type8', 0.0),
        (17, 'server17', 'type8', 0.0),
    ]

    for id, server_name, signal_type, score in test_data:
        server = MCPServerRegistry(id=id, name=server_name)
        db.add(server)
    db.commit()

    for id, server_name, signal_type, score in test_data:
        db.execute(sa.text("""
            INSERT INTO mcp_signal_scores (server_id, signal_type, score)
            VALUES (:server_id, :signal_type, :score)
        """), {'server_id': id, 'signal_type': signal_type, 'score': score})
    db.commit()

    response = test_client.get("/dashboard/signal-scores-distribution")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 8
    for item in data:
        assert 'signal_type' in item
        assert 'score_distribution' in item
        assert 'top_servers' in item
        assert len(item['top_servers']) <= 5

    print("PASS")