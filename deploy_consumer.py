#!/usr/bin/env python3
"""
deploy_consumer.py -- watches shared/outputs/deploy/ for manifest.json,
copies files to their destinations, runs post_deploy commands.
Idempotent. Runs every 30s.
"""
import json, shutil, subprocess, time, logging
from pathlib import Path
from datetime import datetime, timezone

DEPLOY_DIR = Path('/home/workspace/shared/outputs/deploy')
DONE_DIR   = Path('/home/workspace/shared/outputs/deploy/done')
LOG_PATH   = Path('/home/workspace/logs/deploy_consumer.log')

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [deploy_consumer] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(str(LOG_PATH)), logging.StreamHandler()])
log = logging.getLogger(__name__)

def process_manifest(manifest_path: Path):
    manifest = json.loads(manifest_path.read_text())
    log.info('Processing deploy: %s', manifest.get('deploy_at','?'))
    DONE_DIR.mkdir(parents=True, exist_ok=True)

    # Copy files
    for f in manifest.get('files', []):
        src = DEPLOY_DIR / f['name']
        dst = Path(f['dest']) / f['name']
        if not src.exists():
            log.warning('Missing: %s', src)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        log.info('Deployed: %s -> %s', f['name'], dst)

    # Run post_deploy commands
    for cmd in manifest.get('post_deploy', []):
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
            log.info('CMD: %s -> rc=%d', cmd, result.returncode)
        except Exception as e:
            log.warning('CMD failed: %s: %s', cmd, e)

    # Archive manifest
    done = DONE_DIR / f"manifest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    shutil.move(str(manifest_path), str(done))

    # Clean up deployed files
    for f in manifest.get('files', []):
        src = DEPLOY_DIR / f['name']
        if src.exists():
            src.unlink()

    log.info('Deploy complete.')

if __name__ == '__main__':
    log.info('deploy_consumer starting')
    while True:
        try:
            manifest = DEPLOY_DIR / 'manifest.json'
            if manifest.exists():
                process_manifest(manifest)
        except Exception as e:
            log.error('Error: %s', e)
        time.sleep(30)