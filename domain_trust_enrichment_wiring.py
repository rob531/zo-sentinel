#!/usr/bin/env python3
"""
domain_trust_enrichment_wiring.py

Wires the domain_trust_enrichment module into the signal_analyser pipeline.
Registers domain_trust_enrichment as a signal source, enabling it to emit
domain reputation scores to mcp_signal_enrichments.
"""

import json
import os
import sys
import time
import fcntl
import signal
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

# Local import for the domain trust computation module
from domain_trust_enrichment import compute_score


class WriteServiceClient:
    """Client for the write_service API."""
    
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.environ.get(
            'WRITE_SERVICE_URL', 
            'http://localhost:8080'
        )
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'domain-trust-enrichment-wiring/1.0'
        })
    
    def query(self, table: str, conditions: Optional[str] = None) -> List[Dict]:
        """Query data from a table via write_service."""
        payload = {
            'table': table,
            'action': 'query'
        }
        if conditions:
            payload['conditions'] = conditions
        
        try:
            response = self.session.post(
                f'{self.base_url}/write',
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            return result.get('rows', result.get('data', []))
        except requests.RequestException as e:
            print(f"Query error for table {table}: {e}", file=sys.stderr)
            return []
    
    def write(self, table: str, rows: List[Dict]) -> bool:
        """Write rows to a table via write_service."""
        if not rows:
            return True
        
        payload = {
            'table': table,
            'action': 'insert',
            'rows': rows
        }
        
        try:
            response = self.session.post(
                f'{self.base_url}/write',
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Write error for table {table}: {e}", file=sys.stderr)
            return False


class HealthClient:
    """Client for the service_health API."""
    
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.environ.get(
            'SERVICE_HEALTH_URL',
            'http://localhost:8081'
        )
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'domain-trust-enrichment-wiring/1.0'
        })
    
    def send_heartbeat(self, service_name: str, status: str = 'healthy', 
                       metadata: Optional[Dict] = None) -> bool:
        """Send a heartbeat to the service health endpoint."""
        payload = {
            'service': service_name,
            'status': status,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'metadata': metadata or {}
        }
        
        try:
            response = self.session.post(
                f'{self.base_url}/heartbeat',
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Heartbeat error: {e}", file=sys.stderr)
            return False


class DomainTrustEnrichmentWiring:
    """
    Wiring module for domain_trust_enrichment.
    
    Reads server metadata from mcp_server_registry, computes domain trust
    scores, and writes results to mcp_signal_enrichments.
    """
    
    SERVICE_NAME = 'domain_trust_enrichment'
    SIGNAL_TYPE = 'domain_trust'
    HEARTBEAT_INTERVAL = 60  # seconds
    
    def __init__(self, write_service_url: Optional[str] = None,
                 health_service_url: Optional[str] = None,
                 lock_file: str = '/tmp/domain_trust_enrichment.lock'):
        self.write_service = WriteServiceClient(write_service_url)
        self.health_service = HealthClient(health_service_url)
        self.lock_file = lock_file
        self._running = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
    
    def _acquire_lock(self) -> bool:
        """Acquire single-instance lock using file locking."""
        try:
            self._lock_fd = open(self.lock_file, 'w')
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            return True
        except (IOError, OSError) as e:
            print(f"Failed to acquire lock: {e}", file=sys.stderr)
            return False
    
    def _release_lock(self):
        """Release the single-instance lock."""
        if hasattr(self, '_lock_fd') and self._lock_fd:
            try:
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
                self._lock_fd.close()
            except Exception:
                pass
    
    def _get_servers_without_domain_trust(self) -> List[Dict[str, Any]]:
        """
        Query servers from mcp_server_registry that are missing domain_trust
        enrichment using a LEFT JOIN approach.
        """
        # First, get all servers from the registry
        servers = self.write_service.query('mcp_server_registry')
        
        if not servers:
            return []
        
        # Get existing domain_trust entries to filter out
        existing_entries = self.write_service.query(
            'mcp_signal_enrichments',
            f"signal_type='{self.SIGNAL_TYPE}'"
        )
        
        # Create set of server IDs that already have domain_trust enrichment
        enriched_server_ids = set()
        for entry in existing_entries:
            server_id = entry.get('server_id') or entry.get('id')
            if server_id:
                enriched_server_ids.add(server_id)
        
        # Filter servers to only those without enrichment
        servers_without_enrichment = []
        for server in servers:
            server_id = server.get('id') or server.get('server_id')
            if server_id and server_id not in enriched_server_ids:
                # Check if server has the required metadata fields
                if any([
                    server.get('registry_source'),
                    server.get('owner'),
                    server.get('homepage_url'),
                    server.get('repository_url')
                ]):
                    servers_without_enrichment.append(server)
        
        return servers_without_enrichment
    
    def _extract_domain_metadata(self, server: Dict[str, Any]) -> Dict[str, Any]:
        """Extract domain-related metadata from server record."""
        metadata = {}
        
        # Extract homepage URL
        homepage = server.get('homepage_url')
        if homepage:
            metadata['homepage_url'] = homepage
        
        # Extract repository URL
        repo_url = server.get('repository_url')
        if repo_url:
            metadata['repository_url'] = repo_url
        
        # Extract owner/registry info
        owner = server.get('owner')
        if owner:
            metadata['owner'] = owner
        
        # Extract registry source
        registry_source = server.get('registry_source')
        if registry_source:
            metadata['registry_source'] = registry_source
        
        # Additional fields
        for field in ['name', 'server_name', 'display_name']:
            if field in server:
                metadata[field] = server[field]
        
        return metadata
    
    def _compute_and_format_enrichment(self, server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Compute domain trust score for a server and format as enrichment row.
        
        Returns None if computation fails or score is invalid.
        """
        metadata = self._extract_domain_metadata(server)
        
        if not metadata:
            return None
        
        # Compute the domain trust score
        try:
            result = compute_score(metadata)
        except Exception as e:
            print(f"compute_score failed for server {server.get('id')}: {e}", 
                  file=sys.stderr)
            return None
        
        # Validate score range
        score = result.get('score', 0)
        if not (0 <= score <= 100):
            print(f"Invalid score {score} for server {server.get('id')}, "
                  f"expected 0-100", file=sys.stderr)
            return None
        
        # Extract evidence
        evidence = result.get('evidence', {})
        
        # Build the enrichment row
        enrichment_row = {
            'server_id': server.get('id') or server.get('server_id'),
            'signal_type': self.SIGNAL_TYPE,
            'score': score,
            'confidence': result.get('confidence', 0.5),
            'evidence_blob': json.dumps({
                'tld': evidence.get('tld', ''),
                'registry': evidence.get('registry', ''),
                'domain_factors': evidence.get('domain_factors', {}),
                'computation_metadata': {
                    'computed_at': datetime.utcnow().isoformat() + 'Z',
                    'source_fields': list(metadata.keys())
                }
            }),
            'created_at': datetime.utcnow().isoformat() + 'Z'
        }
        
        # Add optional fields if present in result
        if 'factors' in result:
            evidence_data = json.loads(enrichment_row['evidence_blob'])
            evidence_data['factors'] = result['factors']
            enrichment_row['evidence_blob'] = json.dumps(evidence_data)
        
        return enrichment_row
    
    def _heartbeat_loop(self):
        """Background thread for sending periodic heartbeats."""
        while not self._stop_event.is_set():
            self.health_service.send_heartbeat(
                self.SERVICE_NAME,
                status='healthy',
                metadata={
                    'last_cycle': datetime.utcnow().isoformat() + 'Z'
                }
            )
            # Wait for stop event or heartbeat interval
            self._stop_event.wait(self.HEARTBEAT_INTERVAL)
    
    def cycle(self) -> Dict[str, Any]:
        """
        Execute one enrichment cycle.
        
        Reads servers from mcp_server_registry, computes domain trust scores,
        and writes enrichment entries to mcp_signal_enrichments.
        
        Returns a summary of the cycle results.
        """
        cycle_start = datetime.utcnow()
        summary = {
            'cycle_start': cycle_start.isoformat() + 'Z',
            'servers_processed': 0,
            'enrichments_written': 0,
            'enrichments_skipped': 0,
            'errors': 0
        }
        
        # Get servers that need enrichment (idempotent)
        servers = self._get_servers_without_domain_trust()
        summary['servers_processed'] = len(servers)
        
        if not servers:
            print("No servers need domain_trust enrichment")
            return summary
        
        # Process each server
        enrichment_rows = []
        for server in servers:
            try:
                enrichment = self._compute_and_format_enrichment(server)
                if enrichment:
                    enrichment_rows.append(enrichment)
                else:
                    summary['enrichments_skipped'] += 1
            except Exception as e:
                print(f"Error processing server {server.get('id')}: {e}",
                      file=sys.stderr)
                summary['errors'] += 1
        
        # Write all enrichments in batch
        if enrichment_rows:
            success = self.write_service.write(
                'mcp_signal_enrichments',
                enrichment_rows
            )
            if success:
                summary['enrichments_written'] = len(enrichment_rows)
            else:
                summary['errors'] += len(enrichment_rows)
        
        cycle_end = datetime.utcnow()
        summary['cycle_end'] = cycle_end.isoformat() + 'Z'
        summary['duration_seconds'] = (cycle_end - cycle_start).total_seconds()
        
        print(f"Cycle complete: {summary['enrichments_written']} written, "
              f"{summary['enrichments_skipped']} skipped, "
              f"{summary['errors']} errors")
        
        return summary
    
    def start(self):
        """Start the wiring service with heartbeat thread."""
        # Acquire single-instance lock
        if not self._acquire_lock():
            print("Failed to acquire instance lock. Another instance may be running.",
                  file=sys.stderr)
            sys.exit(1)
        
        # Setup signal handlers for graceful shutdown
        def shutdown_handler(signum, frame):
            print(f"\nReceived signal {signum}, shutting down...")
            self.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)
        
        # Start heartbeat thread
        self._running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True
        )
        self._heartbeat_thread.start()
        
        print(f"{self.SERVICE_NAME} wiring service started")
        print(f"Heartbeat interval: {self.HEARTBEAT_INTERVAL}s")
    
    def stop(self):
        """Stop the wiring service."""
        self._running = False
        self._stop_event.set()
        
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
        
        self._release_lock()
        print(f"{self.SERVICE_NAME} wiring service stopped")
    
    def send_heartbeat(self) -> bool:
        """Send a single heartbeat to the health service."""
        return self.health_service.send_heartbeat(
            self.SERVICE_NAME,
            status='healthy',
            metadata={
                'running': self._running,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }
        )


def run_self_test():
    """
    Self-test with synthetic metadata.
    
    Tests compute_score with synthetic data and validates output format.
    """
    print("=" * 60)
    print("Domain Trust Enrichment Wiring - Self Test")
    print("=" * 60)
    
    # Synthetic test cases
    test_cases = [
        {
            'name': 'GitHub well-known repo',
            'metadata': {
                'homepage_url': 'https://github.com/microsoft/vscode',
                'repository_url': 'https://github.com/microsoft/vscode',
                'owner': 'microsoft',
                'registry_source': 'github'
            }
        },
        {
            'name': 'GitLab project',
            'metadata': {
                'homepage_url': 'https://gitlab.com/gitlab-org/gitlab',
                'repository_url': 'https://gitlab.com/gitlab-org/gitlab',
                'owner': 'gitlab-org',
                'registry_source': 'gitlab'
            }
        },
        {
            'name': 'Personal website',
            'metadata': {
                'homepage_url': 'https://example-personal-site.com',
                'repository_url': 'https://github.com/user/repo',
                'owner': 'user'
            }
        },
        {
            'name': 'Minimal metadata',
            'metadata': {
                'owner': 'test-owner'
            }
        }
    ]
    
    all_passed = True
    
    for test_case in test_cases:
        print(f"\nTest: {test_case['name']}")
        print(f"  Input metadata: {test_case['metadata']}")
        
        try:
            result = compute_score(test_case['metadata'])
            print(f"  Result: {result}")
            
            # Validate score range
            score = result.get('score', -1)
            if 0 <= score <= 100:
                print(f"  ✓ Score {score} is within valid range [0, 100]")
            else:
                print(f"  ✗ Score {score} is OUTSIDE valid range [0, 100]")
                all_passed = False
            
            # Validate confidence range
            confidence = result.get('confidence', -1)
            if 0.0 <= confidence <= 1.0:
                print(f"  ✓ Confidence {confidence} is within valid range [0.0, 1.0]")
            else:
                print(f"  ✗ Confidence {confidence} is OUTSIDE valid range [0.0, 1.0]")
                all_passed = False
            
            # Validate evidence has required keys
            evidence = result.get('evidence', {})
            
            if 'tld' in evidence:
                print(f"  ✓ Evidence contains 'tld' key: {evidence.get('tld')}")
            else:
                print(f"  ✗ Evidence MISSING 'tld' key")
                all_passed = False
            
            if 'registry' in evidence:
                print(f"  ✓ Evidence contains 'registry' key: {evidence.get('registry')}")
            else:
                print(f"  ✗ Evidence MISSING 'registry' key")
                all_passed = False
            
        except Exception as e:
            print(f"  ✗ Exception raised: {e}")
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("PASS - All self-test assertions passed")
    else:
        print("FAIL - Some assertions failed")
    print("=" * 60)
    
    return all_passed


if __name__ == '__main__':
    # Run self-test
    test_passed = run_self_test()
    
    if not test_passed:
        sys.exit(1)
    
    # If --daemon flag is provided, run as daemon
    if len(sys.argv) > 1 and sys.argv[1] == '--daemon':
        wiring = DomainTrustEnrichmentWiring()
        wiring.start()
        
        try:
            while wiring._running:
                wiring.cycle()
                time.sleep(300)  # Run cycle every 5 minutes
        except KeyboardInterrupt:
            wiring.stop()
    
    sys.exit(0)