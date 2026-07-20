import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Directive, DirectiveAttempt

router = APIRouter()

def get_retry_burn_report(session: Session, window_hours: int = 72) -> Dict:
    window_start = datetime.utcnow() - timedelta(hours=window_hours)

    # Query directives and their attempts within the window
    directives = session.query(Directive).all()
    report = {
        "directives": [],
        "totals": {
            "directives": 0,
            "wasted_attempts": 0,
            "pct_attempts_wasted": 0.0
        }
    }

    for directive in directives:
        attempts = session.query(DirectiveAttempt).filter(
            DirectiveAttempt.directive_id == directive.id,
            DirectiveAttempt.created_at >= window_start
        ).all()

        if not attempts:
            continue

        ever_passed = any(attempt.status == "passed" for attempt in attempts)
        gates_hit = list({attempt.gate for attempt in attempts if attempt.gate})

        report["directives"].append({
            "directive": directive.name,
            "attempts": len(attempts),
            "ever_passed": ever_passed,
            "gates_hit": gates_hit,
            "retired": directive.retired
        })

    # Calculate totals
    total_attempts = sum(d["attempts"] for d in report["directives"])
    wasted_attempts = sum(
        d["attempts"] for d in report["directives"] if not d["ever_passed"]
    )

    report["totals"] = {
        "directives": len(report["directives"]),
        "wasted_attempts": wasted_attempts,
        "pct_attempts_wasted": (
            (wasted_attempts / total_attempts * 100)
            if total_attempts > 0
            else 0.0
        )
    }

    # Rank by wasted attempts
    report["directives"].sort(
        key=lambda x: x["attempts"] if not x["ever_passed"] else 0,
        reverse=True
    )

    return report

@router.get("/ghost_retry_burn_report")
async def ghost_retry_burn_report(
    window_hours: int = 72,
    session: Session = Depends(get_session)
) -> Dict:
    return get_retry_burn_report(session, window_hours)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory test database
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_session = SessionLocal()
    test_directive = Directive(name="test_directive", retired=False)
    test_session.add(test_directive)
    test_session.commit()

    test_attempt1 = DirectiveAttempt(
        directive_id=test_directive.id,
        status="failed",
        gate="gate1",
        created_at=datetime.utcnow()
    )
    test_attempt2 = DirectiveAttempt(
        directive_id=test_directive.id,
        status="failed",
        gate="gate2",
        created_at=datetime.utcnow()
    )
    test_attempt3 = DirectiveAttempt(
        directive_id=test_directive.id,
        status="passed",
        gate=None,
        created_at=datetime.utcnow()
    )
    test_session.add_all([test_attempt1, test_attempt2, test_attempt3])
    test_session.commit()

    # Test the function
    report = get_retry_burn_report(test_session, window_hours=24)
    assert report["directives"][0]["directive"] == "test_directive"
    assert report["directives"][0]["attempts"] == 3
    assert report["directives"][0]["ever_passed"] is True
    assert report["directives"][0]["gates_hit"] == ["gate1", "gate2"]
    assert report["totals"]["directives"] == 1
    assert report["totals"]["wasted_attempts"] == 0
    assert report["totals"]["pct_attempts_wasted"] == 0.0

    print("PASS")