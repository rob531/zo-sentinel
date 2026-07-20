from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.db import get_session
from app.models import ScoreRunLedger, McpLlmAxisScores
from pydantic import BaseModel

router = APIRouter(prefix="/runs/reconciliation", tags=["reconciliation"])

class ReconciliationReport(BaseModel):
    ledgered_runs: List[int]
    runs_with_imports: List[int]
    orphan_instances: List[int]
    unledgered_runs: List[int]

def get_ledgered_runs(session: Session) -> List[int]:
    return [run.id for run in session.query(ScoreRunLedger).all()]

def get_runs_with_imports(session: Session) -> List[int]:
    return [run.run_id for run in session.query(McpLlmAxisScores).distinct(McpLlmAxisScores.run_id).all()]

def get_orphan_instances(session: Session) -> List[int]:
    ledgered_runs = get_ledgered_runs(session)
    runs_with_imports = get_runs_with_imports(session)
    return [run for run in ledgered_runs if run not in runs_with_imports]

def get_unledgered_runs(session: Session) -> List[int]:
    ledgered_runs = get_ledgered_runs(session)
    runs_with_imports = get_runs_with_imports(session)
    return [run for run in runs_with_imports if run not in ledgered_runs]

@router.get("/", response_model=ReconciliationReport)
async def reconciliation_report(session: Session = Depends(get_session)) -> ReconciliationReport:
    return ReconciliationReport(
        ledgered_runs=get_ledgered_runs(session),
        runs_with_imports=get_runs_with_imports(session),
        orphan_instances=get_orphan_instances(session),
        unledgered_runs=get_unledgered_runs(session)
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, engine
    from app.models import ScoreRunLedger, McpLlmAxisScores

    # Override the session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_ledger = ScoreRunLedger(id=1, run_id=1)
    test_ledger2 = ScoreRunLedger(id=2, run_id=2)
    test_score = McpLlmAxisScores(run_id=1, overall_risk=0.5, auth_strength=0.6,
                                  capability_breadth=0.7, data_sensitivity=0.8,
                                  network_egress=0.9, maintainer_trust=0.4,
                                  exploit_surface=0.3)
    test_score2 = McpLlmAxisScores(run_id=3, overall_risk=0.5, auth_strength=0.6,
                                   capability_breadth=0.7, data_sensitivity=0.8,
                                   network_egress=0.9, maintainer_trust=0.4,
                                   exploit_surface=0.3)

    with TestSession() as session:
        session.add_all([test_ledger, test_ledger2, test_score, test_score2])
        session.commit()

    client = TestClient(app)

    response = client.get("/runs/reconciliation/")
    assert response.status_code == 200
    report = response.json()

    assert report["ledgered_runs"] == [1, 2]
    assert report["runs_with_imports"] == [1, 3]
    assert report["orphan_instances"] == [2]
    assert report["unledgered_runs"] == [3]

    print("PASS")