# deps: requests
import json
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Union

import requests
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

def build_axis_label_index_report(days: int = 30, fetch_rows: Optional[Callable[[], List[Dict]]] = None) -> Dict:
    """
    Build a report of the LLM axis label index.

    Args:
        days: The number of days to look back for data.
        fetch_rows: An optional callable to fetch rows from an in-memory store.

    Returns:
        A dictionary containing the report data.
    """
    if fetch_rows is not None:
        rows = fetch_rows()
    else:
        rows = _fetch_rows_from_db(days)

    axis_data = {}
    for row in rows:
        axis_name = row['axis_name']
        label = row['label']
        label_index = row['label_index']

        if axis_name not in axis_data:
            axis_data[axis_name] = {
                'labels': [],
                'label_index_gaps': [],
                'duplicate_label_indexes': [],
                'unknown_label_indexes': []
            }

        axis_data[axis_name]['labels'].append({
            'label': label,
            'label_index': label_index,
            'count': 1
        })

    for axis_name, data in axis_data.items():
        labels = data['labels']
        label_indices = sorted(set(label['label_index'] for label in labels))

        # Check for gaps in label indices
        if len(label_indices) > 1:
            for i in range(1, len(label_indices)):
                if label_indices[i] - label_indices[i - 1] > 1:
                    data['label_index_gaps'].extend(range(label_indices[i - 1] + 1, label_indices[i]))

        # Check for duplicate label indices
        index_counts = {}
        for label in labels:
            index = label['label_index']
            index_counts[index] = index_counts.get(index, 0) + 1

        for index, count in index_counts.items():
            if count > 1:
                data['duplicate_label_indexes'].append(index)

        # Check for unknown label indices (assuming 0 is the first index)
        if label_indices and label_indices[0] != 0:
            data['unknown_label_indexes'].extend(range(0, label_indices[0]))

    report = {
        'generated_at': datetime.utcnow().isoformat(),
        'days': days,
        'axis_names': sorted(axis_data.keys()),
        'per_axis': [],
        'totals': {
            'rows': len(rows),
            'servers': len(set(row['server_id'] for row in rows))
        }
    }

    for axis_name, data in axis_data.items():
        report['per_axis'].append({
            'axis_name': axis_name,
            'labels': data['labels'],
            'label_index_gaps': sorted(set(data['label_index_gaps'])),
            'duplicate_label_indexes': sorted(set(data['duplicate_label_indexes'])),
            'unknown_label_indexes': sorted(set(data['unknown_label_indexes']))
        })

    return report

def _fetch_rows_from_db(days: int) -> List[Dict]:
    """
    Fetch rows from the database.

    Args:
        days: The number of days to look back for data.

    Returns:
        A list of dictionaries containing the row data.
    """
    session = next(get_session())
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        query = select(McpLlmAxisScore).where(McpLlmAxisScore.scored_at >= cutoff_date)
        rows = session.execute(query).scalars().all()

        return [{
            'axis_name': row.axis_name,
            'label': row.label,
            'label_index': row.label_index,
            'server_id': row.server_id
        } for row in rows]
    finally:
        session.close()

def main() -> int:
    """
    Run the self-test.

    Returns:
        0 if the self-test passes, 1 otherwise.
    """
    def fake_fetch_rows() -> List[Dict]:
        """
        A fake fetch_rows callable for the self-test.

        Returns:
            A list of dictionaries containing the synthetic row data.
        """
        return [
            {'axis_name': 'axis1', 'label': 'label1', 'label_index': 0, 'server_id': 'server1'},
            {'axis_name': 'axis1', 'label': 'label2', 'label_index': 1, 'server_id': 'server1'},
            {'axis_name': 'axis1', 'label': 'label3', 'label_index': 3, 'server_id': 'server1'},
            {'axis_name': 'axis1', 'label': 'label4', 'label_index': 1, 'server_id': 'server1'},
            {'axis_name': 'axis2', 'label': 'label1', 'label_index': 0, 'server_id': 'server2'},
            {'axis_name': 'axis2', 'label': 'label2', 'label_index': 2, 'server_id': 'server2'},
            {'axis_name': 'axis2', 'label': 'label3', 'label_index': 2, 'server_id': 'server2'},
            {'axis_name': 'axis2', 'label': 'label4', 'label_index': 4, 'server_id': 'server2'},
        ]

    report = build_axis_label_index_report(days=30, fetch_rows=fake_fetch_rows)

    # Check that label_index_gaps is non-empty when indices skip a value
    for axis in report['per_axis']:
        if axis['axis_name'] == 'axis1':
            assert 2 in axis['label_index_gaps'], "label_index_gaps is empty when indices skip a value"
        elif axis['axis_name'] == 'axis2':
            assert 1 in axis['label_index_gaps'], "label_index_gaps is empty when indices skip a value"

    # Check that duplicate_label_indexes is detected on a repeated index
    for axis in report['per_axis']:
        if axis['axis_name'] == 'axis1':
            assert 1 in axis['duplicate_label_indexes'], "duplicate_label_indexes is empty when indices are repeated"
        elif axis['axis_name'] == 'axis2':
            assert 2 in axis['duplicate_label_indexes'], "duplicate_label_indexes is empty when indices are repeated"

    # Check that totals.rows equals the number of seeded rows
    assert report['totals']['rows'] == 8, "totals.rows does not equal the number of seeded rows"

    print("PASS")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())