import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import mesh_events

def get_dead_organ_report(cadences: Dict[str, int]) -> List[Dict]:
    session: Session = Depends(get_session)

    # Get the most recent event for each organ
    recent_events = (
        session.query(
            mesh_events.organ,
            func.max(mesh_events.timestamp).label('last_event_ts')
        )
        .group_by(mesh_events.organ)
        .all()
    )

    report = []
    now = datetime.utcnow()

    for organ, last_event_ts in recent_events:
        if last_event_ts is None:
            interval_minutes = None
        else:
            interval = now - last_event_ts
            interval_minutes = interval.total_seconds() / 60

        cadence_minutes = cadences.get(organ)

        if cadence_minutes is None:
            verdict = "UNKNOWN"
        elif interval_minutes is None:
            verdict = "UNKNOWN"
        elif interval_minutes <= cadence_minutes:
            verdict = "LIVE"
        elif interval_minutes <= 2 * cadence_minutes:
            verdict = "LATE"
        else:
            verdict = "DEAD"

        report.append({
            "organ": organ,
            "last_event_ts": last_event_ts.isoformat() if last_event_ts else None,
            "interval_minutes": interval_minutes,
            "cadence_minutes": cadence_minutes,
            "verdict": verdict
        })

    return report

def write_report(report: List[Dict]) -> None:
    report_dir = "/home/workspace/_governance"
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, "dead_organ_report.json")

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

def print_report(report: List[Dict]) -> None:
    print("{:<20} {:<25} {:<15} {:<15} {:<10}".format(
        "organ", "last_event_ts", "interval_minutes", "cadence_minutes", "verdict"
    ))
    for entry in report:
        print("{:<20} {:<25} {:<15} {:<15} {:<10}".format(
            entry["organ"],
            entry["last_event_ts"],
            str(entry["interval_minutes"]) if entry["interval_minutes"] is not None else "None",
            str(entry["cadence_minutes"]) if entry["cadence_minutes"] is not None else "None",
            entry["verdict"]
        ))

def main():
    import argparse
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    parser = argparse.ArgumentParser()
    parser.add_argument("--cadences", type=str, required=True, help="Path to JSON file with cadences")
    args = parser.parse_args()

    with open(args.cadences) as f:
        cadences = json.load(f)

    report = get_dead_organ_report(cadences)
    write_report(report)
    print_report(report)

if __name__ == "__main__":
    import argparse
    from app.db import get_session, Base
    from sqlalchemy import create_engine, Column, String, DateTime
    from sqlalchemy.orm import sessionmaker

    # Self-test setup
    test_cases = [
        {
            "name": "fresh_organ",
            "events": [{"organ": "test_organ1", "timestamp": datetime.utcnow()}],
            "cadences": {"test_organ1": 60},
            "expected_verdict": "LIVE"
        },
        {
            "name": "late_organ",
            "events": [{"organ": "test_organ2", "timestamp": datetime.utcnow() - timedelta(minutes=90)}],
            "cadences": {"test_organ2": 60},
            "expected_verdict": "LATE"
        },
        {
            "name": "dead_organ",
            "events": [{"organ": "test_organ3", "timestamp": datetime.utcnow() - timedelta(minutes=150)}],
            "cadences": {"test_organ3": 60},
            "expected_verdict": "DEAD"
        },
        {
            "name": "unknown_organ",
            "events": [{"organ": "test_organ4", "timestamp": datetime.utcnow()}],
            "cadences": {"test_organ5": 60},
            "expected_verdict": "UNKNOWN"
        }
    ]

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Run test cases
    all_passed = True
    for test_case in test_cases:
        # Seed test data
        session = SessionLocal()
        for event in test_case["events"]:
            session.add(mesh_events(
                organ=event["organ"],
                timestamp=event["timestamp"]
            ))
        session.commit()
        session.close()

        # Run the report
        report = get_dead_organ_report(test_case["cadences"])

        # Check the verdict
        found = False
        for entry in report:
            if entry["organ"] == test_case["events"][0]["organ"]:
                found = True
                if entry["verdict"] != test_case["expected_verdict"]:
                    print(f"FAIL: {test_case['name']} - expected {test_case['expected_verdict']}, got {entry['verdict']}")
                    all_passed = False
                break

        if not found:
            print(f"FAIL: {test_case['name']} - organ not found in report")
            all_passed = False

    if all_passed:
        print("PASS")
        sys.exit(0)
    else:
        sys.exit(1)