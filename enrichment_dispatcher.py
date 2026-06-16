#!/usr/bin/env python3
"""
Enrichment Dispatcher Daemon

Continuously drains the enrichment pipeline by:
1. Reading pending rows from mcp_signal_enrichments (where computed_at IS NULL or enrichment_score IS NULL)
2. Fetching metadata from mcp_signal_scores and context from mcp_server_registry
3. Dispatching to registered enricher modules
4. Writing results back via write_service HTTP POST
"""

import json
import logging
import os
import sys
import time
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('enrichment_dispatcher')

# Configuration from environment
WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE_URL', 'http://localhost:8080')
SERVICE_HEALTH_URL = os.environ.get('SERVICE_HEALTH_URL', 'http://localhost:8080/health')
HEARTBEAT_INTERVAL = 60  # seconds
ENRICHMENTS_DIR = Path(os.environ.get('ENRICHMENTS_DIR', '/home/workspace/zo_sentinel/enrichments'))
WRITE_TIMEOUT = 10  # seconds

# Context fields from mcp_server_registry
REGISTRY_CONTEXT_FIELDS = [
    'mcp_name', 'registry_source', 'age_days', 'download_count',
    'stars', 'publisher_verified', 'dependency_count', 'last_updated', 'tool_schema'
]


class EnrichmentDispatcher:
    """Dispatcher that coordinates enrichment of signal rows."""
    
    def __init__(self):
        self._enrichers: Dict[str, Any] = {}
        self._enrichers_loaded = False
    
    def _load_enrichers(self) -> None:
        """Lazily load enricher modules from the enrichments directory.
        
        No import-time side effects - modules are loaded on demand.
        """
        if self._enrichers_loaded:
            return
            
        if not ENRICHMENTS_DIR.exists():
            logger.warning(f"Enrichments directory not found: {ENRICHMENTS_DIR}")
            self._enrichers_loaded = True
            return
        
        for module_file in ENRICHMENTS_DIR.glob('*.py'):
            if module_file.name.startswith('_'):
                continue
            
            module_name = module_file.stem
            try:
                spec = importlib.util.spec_from_file_location(module_name, module_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    # Don't exec yet - lazy loading
                    self._enrichers[module_name] = {
                        'spec': spec,
                        'module': None,
                        'loaded': False
                    }
                    logger.info(f"Discovered enricher module: {module_name}")
            except Exception as e:
                logger.warning(f"Failed to discover enricher {module_file.name}: {e}")
        
        self._enrichers_loaded = True
    
    def _get_enricher(self, name: str) -> Optional[Any]:
        """Lazily load and return a specific enricher module."""
        if name not in self._enrichers:
            return None
        
        enricher_info = self._enrichers[name]
        if enricher_info['loaded']:
            return enricher_info['module']
        
        try:
            spec = enricher_info['spec']
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            enricher_info['module'] = module
            enricher_info['loaded'] = True
            logger.info(f"Loaded enricher: {name}")
            return module
        except Exception as e:
            logger.error(f"Failed to load enricher {name}: {e}")
            return None
    
    def dispatch(self, metadata: dict) -> Tuple[float, dict]:
        """Dispatch metadata to all registered enrichers and aggregate results.
        
        Args:
            metadata: Dictionary containing signal metadata and registry context
            
        Returns:
            Tuple of (enrichment_score 0-100, evidence dict)
        """
        self._load_enrichers()
        
        total_score = 0.0
        evidence: Dict[str, Any] = {}
        active_enrichers = 0
        
        for name, enricher_info in self._enrichers.items():
            module = self._get_enricher(name)
            if module is None:
                continue
            
            compute_score = getattr(module, 'compute_score', None)
            if not callable(compute_score):
                logger.warning(f"Enricher {name} has no callable compute_score")
                continue
            
            try:
                score, enricher_evidence = compute_score(metadata)
                evidence[name] = enricher_evidence
                total_score += float(score)
                active_enrichers += 1
            except Exception as e:
                logger.warning(f"Enricher {name} raised exception: {e}")
                evidence[name] = {'error': str(e)}
        
        # Normalize score to 0-100 range
        if active_enrichers > 0:
            enrichment_score = min(100.0, max(0.0, total_score / active_enrichers))
        else:
            enrichment_score = 0.0
        
        return enrichment_score, evidence
    
    def _fetch_pending_rows(self) -> List[dict]:
        """Fetch pending enrichment rows from mcp_signal_enrichments."""
        try:
            response = requests.get(
                f"{WRITE_SERVICE_URL}/read",
                params={'table': 'mcp_signal_enrichments'},
                timeout=WRITE_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()
            rows = data.get('rows', [])
            # Filter to pending rows
            return [r for r in rows if r.get('computed_at') is None or r.get('enrichment_score') is None]
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch pending enrichments: {e}")
            return []
    
    def _fetch_signal_metadata(self, signal_id: str) -> dict:
        """Fetch metadata from mcp_signal_scores."""
        try:
            response = requests.get(
                f"{WRITE_SERVICE_URL}/read",
                params={'table': 'mcp_signal_scores', 'id': signal_id},
                timeout=WRITE_TIMEOUT
            )
            if response.status_code == 200:
                return response.json()
            return {}
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to fetch metadata for signal {signal_id}: {e}")
            return {}
    
    def _fetch_registry_context(self, server_id: str) -> dict:
        """Fetch context fields from mcp_server_registry."""
        try:
            response = requests.get(
                f"{WRITE_SERVICE_URL}/read",
                params={'table': 'mcp_server_registry', 'id': server_id},
                timeout=WRITE_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                # Extract only the context fields we need
                return {k: v for k, v in data.items() if k in REGISTRY_CONTEXT_FIELDS}
            return {}
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to fetch registry for server {server_id}: {e}")
            return {}
    
    def _write_enrichment(self, signal_id: str, score: float, evidence: dict, computed_at: str) -> bool:
        """Write enrichment result to mcp_signal_enrichments via write_service."""
        try:
            response = requests.post(
                f"{WRITE_SERVICE_URL}/write",
                json={
                    'table': 'mcp_signal_enrichments',
                    'rows': [{
                        'id': signal_id,
                        'enrichment_score': score,
                        'evidence': json.dumps(evidence),
                        'computed_at': computed_at
                    }]
                },
                timeout=WRITE_TIMEOUT
            )
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to write enrichment for signal {signal_id}: {e}")
            return False
    
    def process_pending_enrichments(self) -> int:
        """Process all pending enrichment rows.
        
        Returns:
            Number of rows successfully processed
        """
        pending_rows = self._fetch_pending_rows()
        if not pending_rows:
            logger.debug("No pending enrichments found")
            return 0
        
        logger.info(f"Found {len(pending_rows)} pending enrichment rows")
        processed = 0
        
        for row in pending_rows:
            signal_id = row.get('id')
            server_id = row.get('server_id')
            
            if not signal_id:
                logger.warning(f"Row missing id field, skipping")
                continue
            
            # Build metadata from signal_scores
            metadata = self._fetch_signal_metadata(signal_id)
            
            # Add context from registry
            if server_id:
                registry_context = self._fetch_registry_context(server_id)
                metadata.update(registry_context)
            
            # Dispatch to enrichers
            try:
                enrichment_score, evidence = self.dispatch(metadata)
            except Exception as e:
                logger.error(f"Failed to dispatch enrichers for signal {signal_id}: {e}")
                enrichment_score = 0.0
                evidence = {'error': str(e)}
            
            # Write result back
            computed_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            if self._write_enrichment(signal_id, enrichment_score, evidence, computed_at):
                processed += 1
                logger.info(f"Processed enrichment for signal {signal_id}, score={enrichment_score:.2f}")
        
        return processed
    
    def send_heartbeat(self) -> None:
        """Send heartbeat to service health endpoint."""
        try:
            requests.post(
                SERVICE_HEALTH_URL,
                json={
                    'service': 'enrichment_dispatcher',
                    'timestamp': time.time(),
                    'status': 'alive'
                },
                timeout=WRITE_TIMEOUT
            )
            logger.debug("Heartbeat sent successfully")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to send heartbeat: {e}")


def run() -> None:
    """Main daemon loop."""
    dispatcher = EnrichmentDispatcher()
    logger.info("Enrichment dispatcher starting...")
    
    while True:
        try:
            # Send heartbeat
            dispatcher.send_heartbeat()
            
            # Process pending enrichments
            processed = dispatcher.process_pending_enrichments()
            if processed > 0:
                logger.info(f"Cycle complete: processed {processed} rows")
            
        except Exception as e:
            logger.error(f"Error in main loop cycle: {e}")
        
        # Sleep until next cycle
        time.sleep(HEARTBEAT_INTERVAL)


def run_tests() -> None:
    """Run acceptance tests with 3 known-good inputs."""
    import unittest.mock
    
    print("=" * 60)
    print("ENRICHMENT DISPATCHER ACCEPTANCE TESTS")
    print("=" * 60)
    
    # Test inputs - known-good metadata samples
    test_inputs = [
        {
            'signal_id': 'test_signal_001',
            'metadata': {
                'score': 85,
                'category': 'performance',
                'tool_name': 'code-complete',
                'author': 'test-author',
                'version': '1.0.0'
            },
            'registry': {
                'mcp_name': 'code-complete',
                'registry_source': 'npm',
                'age_days': 180,
                'download_count': 50000,
                'stars': 1200,
                'publisher_verified': True,
                'dependency_count': 5,
                'last_updated': '2024-01-15',
                'tool_schema': {'input': 'string', 'output': 'string'}
            }
        },
        {
            'signal_id': 'test_signal_002',
            'metadata': {
                'score': 92,
                'category': 'reliability',
                'tool_name': 'db-migrator',
                'author': 'test-author',
                'version': '2.1.0'
            },
            'registry': {
                'mcp_name': 'db-migrator',
                'registry_source': 'github',
                'age_days': 365,
                'download_count': 150000,
                'stars': 3500,
                'publisher_verified': True,
                'dependency_count': 3,
                'last_updated': '2024-02-20',
                'tool_schema': {'input': 'object', 'output': 'object'}
            }
        },
        {
            'signal_id': 'test_signal_003',
            'metadata': {
                'score': 78,
                'category': 'usability',
                'tool_name': 'log-analyzer',
                'author': 'another-author',
                'version': '0.9.0'
            },
            'registry': {
                'mcp_name': 'log-analyzer',
                'registry_source': 'pypi',
                'age_days': 45,
                'download_count': 5000,
                'stars': 200,
                'publisher_verified': False,
                'dependency_count': 8,
                'last_updated': '2024-03-01',
                'tool_schema': {'input': 'array', 'output': 'json'}
            }
        }
    ]
    
    dispatcher = EnrichmentDispatcher()
    
    for i, test_input in enumerate(test_inputs, 1):
        print(f"\nTest {i}: signal_id={test_input['signal_id']}")
        print(f"  metadata keys: {list(test_input['metadata'].keys())}")
        
        # Combine metadata with registry context
        combined_metadata = {**test_input['metadata'], **test_input['registry']}
        
        # Dispatch to enrichers
        enrichment_score, evidence = dispatcher.dispatch(combined_metadata)
        
        # Validate results
        assert isinstance(enrichment_score, (int, float)), \
            f"Test {i}: enrichment_score should be numeric, got {type(enrichment_score)}"
        assert 0 <= enrichment_score <= 100, \
            f"Test {i}: enrichment_score should be 0-100, got {enrichment_score}"
        assert isinstance(evidence, dict), \
            f"Test {i}: evidence should be dict, got {type(evidence)}"
        assert len(evidence) > 0, \
            f"Test {i}: evidence should be non-empty"
        
        print(f"  enrichment_score: {enrichment_score}")
        print(f"  evidence keys: {list(evidence.keys())}")
        print(f"  ✓ PASS")
    
    print("\n" + "=" * 60)
    print("ALL ACCEPTANCE TESTS PASSED")
    print("=" * 60)


if __name__ == '__main__':
    run_tests()