import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Orgs, Users
from fastapi import Depends
from sqlalchemy import func, or_

def compute_stall_rate(records: List[Dict]) -> Dict[str, float]:
    escalated_records = [r for r in records if r.get('escalated', False)]
    if not escalated_records:
        return {'fleet': 0.0, 'per_organ': {}}

    total_escalated = len(escalated_records)
    allowed_escalated = sum(1 for r in escalated_records if r['verdict'] == 'ALLOW')

    stall_rate = allowed_escalated / total_escalated

    per_organ = defaultdict(list)
    for r in escalated_records:
        per_organ[r['organ']].append(r)

    per_organ_stall = {}
    for organ, org_records in per_organ.items():
        total_org_escalated = len(org_records)
        allowed_org_escalated = sum(1 for r in org_records if r['verdict'] == 'ALLOW')
        per_organ_stall[organ] = allowed_org_escalated / total_org_escalated if total_org_escalated else 0.0

    return {'fleet': stall_rate, 'per_organ': per_organ_stall}

def compute_decision_mix(records: List[Dict]) -> Dict[str, Dict[str, int]]:
    fleet_mix = defaultdict(int)
    per_organ_mix = defaultdict(lambda: defaultdict(int))

    for r in records:
        fleet_mix[r['verdict']] += 1
        per_organ_mix[r['organ']][r['verdict']] += 1

    return {
        'fleet': dict(fleet_mix),
        'per_organ': {k: dict(v) for k, v in per_organ_mix.items()}
    }

def compute_unknown_classes(records: List[Dict]) -> Dict[str, List[str]]:
    unknown_classes = set()
    per_organ_unknown = defaultdict(set)

    for r in records:
        if 'UNKNOWN action class' in r.get('reason', ''):
            unknown_classes.add(r['action_class'])
            per_organ_unknown[r['organ']].add(r['action_class'])

    return {
        'fleet': sorted(unknown_classes),
        'per_organ': {k: sorted(v) for k, v in per_organ_unknown.items()}
    }

def compute_never_refused(records: List[Dict]) -> Dict[str, List[str]]:
    class_verdicts = defaultdict(set)
    per_organ_class_verdicts = defaultdict(lambda: defaultdict(set))

    for r in records:
        class_verdicts[r['action_class']].add(r['verdict'])
        per_organ_class_verdicts[r['organ']][r['action_class']].add(r['verdict'])

    never_refused = [
        cls for cls, verdicts in class_verdicts.items()
        if verdicts == {'ALLOW'}
    ]

    per_organ_never_refused = {}
    for organ, org_classes in per_organ_class_verdicts.items():
        per_organ_never_refused[organ] = [
            cls for cls, verdicts in org_classes.items()
            if verdicts == {'ALLOW'}
        ]

    return {
        'fleet': never_refused,
        'per_organ': per_organ_never_refused
    }

def generate_report(records: List[Dict]) -> Dict:
    if not records:
        return {
            'stall_rate': {'fleet': 0.0, 'per_organ': {}},
            'decision_mix': {'fleet': {}, 'per_organ': {}},
            'unknown_classes': {'fleet': [], 'per_organ': {}},
            'never_refused': {'fleet': [], 'per_organ': {}},
            'timestamp_range': None,
            'record_count': 0
        }

    timestamps = [r['ts'] for r in records]
    timestamp_range = (min(timestamps), max(timestamps))

    return {
        'stall_rate': compute_stall_rate(records),
        'decision_mix': compute_decision_mix(records),
        'unknown_classes': compute_unknown_classes(records),
        'never_refused': compute_never_refused(records),
        'timestamp_range': timestamp_range,
        'record_count': len(records)
    }

def write_report(report: Dict) -> None:
    report_dir = '/home/workspace/_governance'
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, 'authority_report.json')

    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)

    print("Authority Log Report Summary:")
    print(f"Timestamp Range: {report['timestamp_range'][0]} to {report['timestamp_range'][1]}")
    print(f"Total Records: {report['record_count']}")
    print(f"Fleet Stall Rate: {report['stall_rate']['fleet']:.2f} ({report['stall_rate']['fleet'] * 100:.0f}%)")
    print("Per Organ Stall Rates:")
    for organ, rate in report['stall_rate']['per_organ'].items():
        print(f"  {organ}: {rate:.2f} ({rate * 100:.0f}%)")
    print("\nFleet Decision Mix:")
    for verdict, count in report['decision_mix']['fleet'].items():
        print(f"  {verdict}: {count}")
    print("\nPer Organ Decision Mix:")
    for organ, mix in report['decision_mix']['per_organ'].items():
        print(f"  {organ}:")
        for verdict, count in mix.items():
            print(f"    {verdict}: {count}")
    print("\nFleet Unknown Classes:")
    for cls in report['unknown_classes']['fleet']:
        print(f"  {cls}")
    print("\nPer Organ Unknown Classes:")
    for organ, classes in report['unknown_classes']['per_organ'].items():
        print(f"  {organ}:")
        for cls in classes:
            print(f"    {cls}")
    print("\nFleet Never Refused Classes:")
    for cls in report['never_refused']['fleet']:
        print(f"  {cls}")
    print("\nPer Organ Never Refused Classes:")
    for organ, classes in report['never_refused']['per_organ'].items():
        print(f"  {organ}:")
        for cls in classes:
            print(f"    {cls}")

def read_authority_log(log_path: str) -> List[Dict]:
    with open(log_path, 'r') as f:
        return [json.loads(line) for line in f]

def main():
    log_path = os.getenv('ZO_AUTHORITY_LOG', '/home/workspace/_governance/authority_log.jsonl')
    records = read_authority_log(log_path)
    report = generate_report(records)
    write_report(report)

if __name__ == '__main__':
    # Self-test
    test_records = [
        {
            "verdict": "ALLOW",
            "action_class": "class1",
            "reason": "UNKNOWN action class",
            "recoverable_alternative": True,
            "organ": "org1",
            "ts": "2023-01-01T00:00:00",
            "run_id": "run1",
            "work_item": "item1",
            "escalated": True,
            "f1_stall": True
        },
        {
            "verdict": "ASK",
            "action_class": "class2",
            "reason": "Known reason",
            "recoverable_alternative": False,
            "organ": "org1",
            "ts": "2023-01-01T00:01:00",
            "run_id": "run2",
            "work_item": "item2",
            "escalated": True
        },
        {
            "verdict": "ALLOW",
            "action_class": "class3",
            "reason": "Another reason",
            "recoverable_alternative": True,
            "organ": "org1",
            "ts": "2023-01-01T00:02:00",
            "run_id": "run3",
            "work_item": "item3",
            "escalated": False
        },
        {
            "verdict": "ALLOW",
            "action_class": "class4",
            "reason": "Known reason",
            "recoverable_alternative": False,
            "organ": "org2",
            "ts": "2023-01-01T00:03:00",
            "run_id": "run4",
            "work_item": "item4",
            "escalated": False
        },
        {
            "verdict": "COFC",
            "action_class": "class1",
            "reason": "UNKNOWN action class",
            "recoverable_alternative": True,
            "organ": "org2",
            "ts": "2023-01-01T00:04:00",
            "run_id": "run5",
            "work_item": "item5",
            "escalated": True
        }
    ]

    expected_report = {
        'stall_rate': {'fleet': 0.5, 'per_organ': {'org1': 0.5, 'org2': 0.0}},
        'decision_mix': {
            'fleet': {'ALLOW': 3, 'ASK': 1, 'COFC': 1},
            'per_organ': {
                'org1': {'ALLOW': 2, 'ASK': 1},
                'org2': {'ALLOW': 1, 'COFC': 1}
            }
        },
        'unknown_classes': {
            'fleet': ['class1'],
            'per_organ': {'org1': [], 'org2': ['class1']}
        },
        'never_refused': {
            'fleet': ['class3', 'class4'],
            'per_organ': {'org1': ['class3'], 'org2': ['class4']}
        },
        'timestamp_range': ('2023-01-01T00:00:00', '2023-01-01T00:04:00'),
        'record_count': 5
    }

    report = generate_report(test_records)
    if report == expected_report:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        print(f"Expected: {expected_report}")
        print(f"Actual: {report}")
        sys.exit(1)