#!/usr/bin/env python3
import os
import re
import sys
import time
from datetime import datetime, timedelta
import requests

def get_last_line_with_pattern(log_path, pattern):
    try:
        with open(log_path, 'r') as f:
            lines = f.readlines()
            for line in reversed(lines):
                if re.search(pattern, line):
                    return line.strip()
        return None
    except FileNotFoundError:
        return None

def get_stage_status():
    status = {
        'generator': {'status': 'STALE', 'evidence': None},
        'promoter': {'status': 'STALE', 'evidence': None},
        'goose_runner': {'status': 'STALE', 'evidence': None},
        'publisher': {'status': 'STALE', 'evidence': None}
    }

    logs_dir = '/home/workspace/logs'

    # Generator
    generator_log = os.path.join(logs_dir, 'directive_generator_goose.log')
    evidence = get_last_line_with_pattern(generator_log, r'proposed_depth')
    if evidence:
        status['generator']['status'] = 'OK'
        status['generator']['evidence'] = evidence

    # Promoter
    promoter_log = os.path.join(logs_dir, 'proposed_to_pending_promoter.log')
    evidence = get_last_line_with_pattern(promoter_log, r'scanned=\d+/eligible=\d+/promoted=\d+')
    if evidence:
        status['promoter']['status'] = 'OK'
        status['promoter']['evidence'] = evidence

    # Goose Runner
    goose_log = os.path.join(logs_dir, 'goose_runner.log')
    evidence = get_last_line_with_pattern(goose_log, r'Total directives loaded')
    if evidence:
        if 'building' in evidence.lower():
            status['goose_runner']['status'] = 'OK'
        else:
            status['goose_runner']['status'] = 'WARN'
        status['goose_runner']['evidence'] = evidence

    # Publisher
    publisher_log = os.path.join(logs_dir, 'pr_publisher.log')
    evidence = get_last_line_with_pattern(publisher_log, r'\[.*\]')
    if evidence:
        status['publisher']['status'] = 'OK'
        status['publisher']['evidence'] = evidence

    return status

def get_funnel_counts():
    counts = {'build_artifact': 0, 'pr_published': 0}

    try:
        now = datetime.utcnow()
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')

        queries = {
            'build_artifact': f"SELECT COUNT(*) FROM build_artifact WHERE created_at > '{yesterday}'",
            'pr_published': f"SELECT COUNT(*) FROM pr_published WHERE created_at > '{yesterday}'"
        }

        for key, query in queries.items():
            response = requests.post(
                'http://127.0.0.1:8772/query',
                json={'sql': query},
                timeout=5
            )
            if response.status_code == 200:
                counts[key] = response.json().get('data', [0])[0]
    except requests.RequestException:
        pass

    return counts

def print_status_table(status, counts):
    print("\nPipeline Health Check")
    print("=" * 40)
    print(f"{'Stage':<15} | {'Status':<10} | {'Evidence'}")
    print("-" * 40)
    for stage, data in status.items():
        print(f"{stage:<15} | {data['status']:<10} | {data['evidence']}")
    print("-" * 40)
    print(f"Funnel Counts (last 24h):")
    print(f"  Build Artifacts: {counts['build_artifact']}")
    print(f"  PRs Published:   {counts['pr_published']}")
    print("=" * 40)

def main():
    status = get_stage_status()
    counts = get_funnel_counts()

    print_status_table(status, counts)

    if any(s['status'] == 'STALE' for s in status.values()):
        sys.exit(1)

if __name__ == '__main__':
    main()