import os
import time
from datetime import datetime
from typing import List, Dict, Optional
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry

class Directive(BaseModel):
    task: str
    file: str
    age_seconds: int

class StarvationMetrics(BaseModel):
    min_s: float
    p25_s: float
    p50_s: float
    p75_s: float
    max_s: float
    old_count: int
    threshold_s: int

class Response(BaseModel):
    starvation: StarvationMetrics
    directives: List[Directive]

def get_directive_files(directory: str) -> List[Dict[str, str]]:
    files = []
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        if os.path.isfile(filepath):
            mtime = os.path.getmtime(filepath)
            task = filename.split('.')[0]  # Assuming filename format is <task>.<ext>
            files.append({
                'task': task,
                'file': filename,
                'mtime': mtime
            })
    return files

def compute_quartiles(ages: List[float]) -> Dict[str, float]:
    if not ages:
        return {
            'min_s': 0,
            'p25_s': 0,
            'p50_s': 0,
            'p75_s': 0,
            'max_s': 0
        }

    ages_sorted = sorted(ages)
    n = len(ages_sorted)

    return {
        'min_s': ages_sorted[0],
        'p25_s': ages_sorted[int(0.25 * n)] if n > 0 else 0,
        'p50_s': ages_sorted[int(0.5 * n)] if n > 0 else 0,
        'p75_s': ages_sorted[int(0.75 * n)] if n > 0 else 0,
        'max_s': ages_sorted[-1]
    }

def get_starvation_metrics(
    pending_dir: str,
    proposed_dir: str,
    threshold_s: int = 3600
) -> Response:
    now = time.time()
    pending_files = get_directive_files(pending_dir)
    proposed_files = get_directive_files(proposed_dir)

    all_files = pending_files + proposed_files
    ages = []
    directives = []

    for file_info in all_files:
        age = now - file_info['mtime']
        ages.append(age)
        directives.append({
            'task': file_info['task'],
            'file': file_info['file'],
            'age_seconds': int(age)
        })

    quartiles = compute_quartiles(ages)
    old_count = sum(1 for age in ages if age > threshold_s)

    return Response(
        starvation=StarvationMetrics(
            min_s=quartiles['min_s'],
            p25_s=quartiles['p25_s'],
            p50_s=quartiles['p50_s'],
            p75_s=quartiles['p75_s'],
            max_s=quartiles['max_s'],
            old_count=old_count,
            threshold_s=threshold_s
        ),
        directives=directives
    )

if __name__ == "__main__":
    import tempfile
    import shutil

    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()

    # Create test files with different ages
    now = time.time()
    file1 = os.path.join(temp_dir, "task1.txt")
    file2 = os.path.join(temp_dir, "task2.txt")
    file3 = os.path.join(temp_dir, "task3.txt")

    with open(file1, 'w') as f:
        f.write("test")
    os.utime(file1, (now - 10, now - 10))

    with open(file2, 'w') as f:
        f.write("test")
    os.utime(file2, (now - 120, now - 120))

    with open(file3, 'w') as f:
        f.write("test")
    os.utime(file3, (now - 7200, now - 7200))

    # Test the function
    response = get_starvation_metrics(temp_dir, temp_dir, threshold_s=3600)

    # Assertions
    assert response.starvation.max_s >= 7000
    assert response.starvation.old_count >= 1

    # Clean up
    shutil.rmtree(temp_dir)

    print("PASS")