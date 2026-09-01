#!/usr/bin/env python3
"""
enrichments_writer.py - Daemon that writes enrichment scores to mcp_signal_enrichments table.

Consumes enrichment outputs from modules (supply_chain_enrichment, community_signal_enrichment, etc.)
and persists them to the DB via write_service at 127.0.0.1:8772.

This is the MISSING WRITER that explains why mcp_signal_enrichments has 0 rows
despite enrichment modules existing.
"""

import json
import time
import logging
import importlib
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from threading import Thread, Event
from queue import Queue, Empty

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60  # seconds
POLL_INTERVAL = 5  # seconds
ENRICHMENT_TIMEOUT = 10  # seconds
WRITE_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
BACKOFF_FACTOR = 2

# Enrichment module mapping
ENRICHMENT_MODULES = {
    'supply_chain': 'supply_chain_enrichment',
    'community_signal': 'community_signal_enrichment',
}


@dataclass
class EnrichmentRecord:
    """Represents a single enrichment record to be written to the DB."""
    server_id: str
    signal_type: str
    score: float
    evidence_blob: dict
    computed_at: str = field(default="")
    
    def __post_init__(self):
        if not self.computed_at:
            self.computed_at = datetime.utcnow().isoformat() + "Z"
    
    def to_dict(self) -> dict:
        return {
            "server_id": self.server_id,
            "signal_type": self.signal_type,
            "computed_at": self.computed_at,
            "score": self.score,
            "evidence_blob": json.dumps(self.evidence_blob)
        }


class EnrichmentsWriter:
    """
    Daemon that reads computed enrichment scores and writes them to mcp_signal_enrichments.
    
    This writer is responsible for:
    1. Polling the work queue for pending enrichment tasks
    2. Computing enrichment scores using registered modules
    3. Writing results to mcp_signal_enrichments via write_service
    4. Maintaining a heartbeat to service_health
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.last_heartbeat: Optional[datetime] = None
        self._running = False
        self._heartbeat_thread: Optional[Thread] = None
        self._stop_event = Event()
        self._enrichment_modules: Dict[str, Any] = {}
        self._pending_queue: Queue = Queue()
        
        # Load enrichment modules dynamically
        self._load_enrichment_modules()
    
    def _load_enrichment_modules(self) -> None:
        """Load enrichment modules dynamically."""
        for name, module_name in ENRICHMENT_MODULES.items():
            try:
                module = importlib.import_module(module_name)
                self._enrichment_modules[name] = module
                logger.info(f"Loaded enrichment module: {name} from {module_name}")
            except ImportError as e:
                logger.warning(f"Could not load {module_name}: {e}")
    
    def _query_service(self, sql: str, timeout: int = ENRICHMENT_TIMEOUT) -> List[Dict[str, Any]]:
        """
        Query write_service via POST to /query endpoint.
        """
        try:
            response = self.session.post(
                f"{WRITE_SERVICE_URL}/query",
                json={"sql": sql},
                timeout=timeout
            )
            response.raise_for_status()
            data = response.json()
            return data.get("rows", [])
        except requests.RequestException as e:
            logger.error(f"Query failed: {e}")
            return []
    
    def _write_with_retry(self, rows: List[Dict[str, Any]]) -> bool:
        """
        Write rows to write_service with exponential backoff retry.
        """
        payload = {
            "table": "mcp_signal_enrichments",
            "rows": rows
        }
        
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(
                    f"{WRITE_SERVICE_URL}/write",
                    json=payload,
                    timeout=WRITE_TIMEOUT
                )
                
                if response.status_code >= 500:
                    # Server error, retry with backoff
                    raise requests.HTTPError(f"Server error: {response.status_code}")
                
                if response.status_code == 200:
                    return True
                    
            except (requests.RequestException, requests.HTTPError) as e:
                logger.warning(f"Write attempt {attempt + 1}/{MAX_RETRIES} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    sleep_time = BACKOFF_FACTOR ** attempt
                    logger.info(f"Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
        
        logger.error(f"Write failed after {MAX_RETRIES} attempts")
        return False
    
    def poll_for_pending_enrichments(self) -> List[Dict[str, Any]]:
        """UNRESOLVED REFERENT -- the work queue has no producer, anywhere.

        This selected `server_id, enrichment_type, score, evidence_blob FROM
        mcp_enrichment_work_queue WHERE written = 0`. That table exists on no
        plane -- not on the bus, not as a __tablename__, in no migration -- and
        NOTHING IN THIS REPOSITORY EVER WRITES ONE. The only two references to
        the name in the whole tree were this SELECT and the UPDATE that marks a
        row processed: a consumer and an acknowledger, with no producer.

        There is no near-miss to correct it to, and that distinguishes it from
        the other names on the #4080 list. The closest real table,
        `mcp_signal_enrichments`, is this writer's OUTPUT -- write_enrichment()
        writes it -- so reading it as the input queue would make the daemon feed
        on itself, and it carries no `written` column to drive the handshake.
        No assignment of real tables makes this query true.

        So the intended referent is recorded as UNRESOLVED rather than guessed.
        A plausible wrong name is how this backlog was created; it passes the
        check and hides forever, where an honest unresolved stays visible.

        Behaviour is UNCHANGED. _query_service() has always raised on the
        missing table and returned [], so this has never yielded a single item
        and run() has always fallen through to
        _compute_and_write_missing_enrichments(), which is the path that
        actually works. What changes is that the module no longer NAMES a table
        that does not exist.

        TO REBUILD: a queue needs a writer. Whatever enqueues enrichment work
        must declare its own table (a migration, or an ensure_tables() in the
        producer) before this consumer can be restored. Refs #4080.
        """
        return []
    
    def _get_server_metadata(self, server_id: str) -> Optional[Dict[str, Any]]:
        """
        Get server metadata from the registry.
        """
        sql = f"SELECT * FROM mcp_server_registry WHERE server_id = '{server_id}'"
        results = self._query_service(sql)
        return results[0] if results else None
    
    def _get_servers_needing_enrichment(self, signal_type: str) -> List[str]:
        """
        Get servers in registry that lack enrichment for the given signal type.
        """
        sql = f"""
            SELECT sr.server_id 
            FROM mcp_server_registry sr
            LEFT JOIN mcp_signal_enrichments se ON sr.server_id = se.server_id 
                AND se.signal_type = '{signal_type}'
            WHERE se.server_id IS NULL
        """
        results = self._query_service(sql)
        return [r['server_id'] for r in results]
    
    def _compute_enrichment(self, signal_type: str, metadata: dict) -> Tuple[float, dict]:
        """
        Compute enrichment score using the appropriate module.
        
        Returns:
            Tuple of (score, evidence_dict)
        """
        if signal_type not in self._enrichment_modules:
            logger.warning(f"No module registered for signal type: {signal_type}")
            return 0.0, {}
        
        module = self._enrichment_modules[signal_type]
        
        # Check if module has compute_score function
        if hasattr(module, 'compute_score'):
            try:
                return module.compute_score(metadata)
            except Exception as e:
                logger.error(f"Error computing {signal_type} enrichment: {e}")
                return 0.0, {}
        
        # Check if module has a class with compute_score method
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and hasattr(attr, 'compute_score'):
                try:
                    instance = attr()
                    return instance.compute_score(metadata)
                except Exception as e:
                    logger.error(f"Error computing {signal_type} enrichment: {e}")
                    return 0.0, {}
        
        logger.warning(f"No compute_score found for signal type: {signal_type}")
        return 0.0, {}
    
    def write_enrichment(self, server_id: str, signal_type: str, score: float, evidence: dict) -> bool:
        """
        Write a single enrichment record to mcp_signal_enrichments.
        
        Args:
            server_id: The server identifier
            signal_type: Type of enrichment signal
            score: Computed enrichment score
            evidence: Evidence dict for the score
            
        Returns:
            True if write succeeded, False otherwise
        """
        record = EnrichmentRecord(
            server_id=server_id,
            signal_type=signal_type,
            score=score,
            evidence_blob=evidence
        )
        
        return self._write_with_retry([record.to_dict()])
    
    def _process_queue_items(self, items: List[Dict[str, Any]]) -> None:
        """
        Process items from the enrichment work queue.
        """
        for item in items:
            server_id = item.get('server_id')
            signal_type = item.get('enrichment_type')
            score = item.get('score', 0.0)
            
            # Parse evidence blob if it's a string
            evidence = item.get('evidence_blob', {})
            if isinstance(evidence, str):
                try:
                    evidence = json.loads(evidence)
                except json.JSONDecodeError:
                    evidence = {}
            
            # Write the enrichment
            if self.write_enrichment(server_id, signal_type, score, evidence):
                self._mark_queue_item_processed(server_id, signal_type)
                logger.info(f"Wrote enrichment for {server_id}: {signal_type}")
            else:
                logger.error(f"Failed to write enrichment for {server_id}: {signal_type}")
    
    def _compute_and_write_missing_enrichments(self) -> None:
        """
        Compute enrichment scores for servers that lack them.
        """
        for signal_type in self._enrichment_modules.keys():
            servers = self._get_servers_needing_enrichment(signal_type)
            
            for server_id in servers:
                metadata = self._get_server_metadata(server_id)
                if not metadata:
                    logger.warning(f"No metadata found for server: {server_id}")
                    continue
                
                score, evidence = self._compute_enrichment(signal_type, metadata)
                
                if self.write_enrichment(server_id, signal_type, score, evidence):
                    logger.info(f"Computed and wrote enrichment for {server_id}: {signal_type}={score}")
                else:
                    logger.error(f"Failed to write computed enrichment for {server_id}")
    
    def _mark_queue_item_processed(self, server_id: str, enrichment_type: str) -> None:
        """Acknowledge a queue item. UNREACHABLE while the queue is unresolved.

        Its only caller is _process_queue_items(), which is only fed by
        poll_for_pending_enrichments() -- see that docstring: the queue table
        has no producer on any plane, so this is never called. The
        acknowledgement is kept, not deleted, because it is half of a handshake
        that a future producer would need; it just cannot name a table that
        does not exist while it waits. Refs #4080.
        """
        logger.debug(
            "queue ack skipped for %s/%s: mcp_enrichment_work_queue is an "
            "UNRESOLVED referent (no producer on any plane)",
            server_id, enrichment_type)
        return
        # Restored when a producer declares the queue table:
        #   UPDATE <queue table> SET written = 1
        #    WHERE server_id = ? AND enrichment_type = ?
    
    def _send_heartbeat(self) -> bool:
        """
        Send heartbeat to service_health.
        """
        try:
            response = self.session.post(
                f"{WRITE_SERVICE_URL}/heartbeat",
                json={
                    "service": "enrichments_writer",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "status": "running"
                },
                timeout=ENRICHMENT_TIMEOUT
            )
            self.last_heartbeat = datetime.utcnow()
            return response.status_code == 200
        except requests.RequestException as e:
            logger.warning(f"Heartbeat failed: {e}")
            return False
    
    def _heartbeat_loop(self) -> None:
        """
        Background thread for sending heartbeats.
        """
        while not self._stop_event.is_set():
            self._send_heartbeat()
            # Wait for stop event or heartbeat interval
            self._stop_event.wait(timeout=HEARTBEAT_INTERVAL)
    
    def run(self) -> None:
        """
        Main daemon loop.
        
        Continuously:
        1. Sends heartbeat to service_health
        2. Polls for pending enrichments from the work queue
        3. Writes enrichments to mcp_signal_enrichments
        4. Computes missing enrichments for registered servers
        """
        logger.info("Starting enrichments_writer daemon")
        self._running = True
        self._stop_event.clear()
        
        # Start heartbeat thread
        self._heartbeat_thread = Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        
        try:
            while self._running:
                try:
                    # Process items from the work queue
                    pending = self.poll_for_pending_enrichments()
                    if pending:
                        logger.info(f"Found {len(pending)} pending queue items")
                        self._process_queue_items(pending)
                    
                    # Compute and write missing enrichments
                    self._compute_and_write_missing_enrichments()
                    
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                    # Heartbeat must fire even if enrichment computation fails
                    self._send_heartbeat()
                
                # Wait before next poll
                time.sleep(POLL_INTERVAL)
                
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop()
    
    def stop(self) -> None:
        """
        Stop the daemon gracefully.
        """
        logger.info("Stopping enrichments_writer daemon")
        self._running = False
        self._stop_event.set()
        
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
        
        logger.info("Enrichments_writer daemon stopped")


def run():
    """Entry point for the daemon."""
    writer = EnrichmentsWriter()
    writer.run()


if __name__ == '__main__':
    # Self-test for the enrichments_writer
    import sys
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from threading import Thread
    from urllib.parse import urlparse, parse_qs
    
    # Track test state
    test_state = {
        'query_called': False,
        'write_called': False,
        'write_payload': None,
        'query_response': [
            {
                'server_id': 'test-server-001',
                'enrichment_type': 'supply_chain',
                'score': 0.85,
                'evidence_blob': '{"suppliers": ["supplier-a", "supplier-b"]}'
            }
        ]
    }
    
    class MockRequestHandler(BaseHTTPRequestHandler):
        """Mock handler for write_service endpoints."""
        
        def log_message(self, format, *args):
            # Suppress HTTP server logs during tests
            pass
        
        def do_POST(self):
            parsed = urlparse(self.path)
            
            if parsed.path == '/query':
                # Return pending enrichment record
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                response = {'rows': test_state['query_response']}
                self.wfile.write(json.dumps(response).encode())
                test_state['query_called'] = True
                
            elif parsed.path == '/write':
                # Capture write payload
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                test_state['write_payload'] = json.loads(body.decode())
                test_state['write_called'] = True
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())
                
            else:
                self.send_response(404)
                self.end_headers()
        
        def do_GET(self):
            # Health check endpoint
            if self.path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode())
            else:
                self.send_response(404)
                self.end_headers()
    
    # Start mock server
    mock_server = HTTPServer(('127.0.0.1', 8772), MockRequestHandler)
    mock_thread = Thread(target=mock_server.serve_forever, daemon=True)
    mock_thread.start()
    
    try:
        # Create writer instance
        writer = EnrichmentsWriter()
        
        # Test 1: poll_for_pending_enrichments yields nothing.
        #
        # It used to assert one mock item against a mock /query endpoint, which
        # is how a query against a table that exists on NO plane passed its own
        # self-test for months: the mock answered a question the real bus
        # cannot. See the docstring on poll_for_pending_enrichments -- the work
        # queue has no producer anywhere, so the referent is UNRESOLVED and the
        # method now says so instead of naming a phantom table. Refs #4080.
        pending = writer.poll_for_pending_enrichments()
        assert pending == [], f"Expected no pending items, got {pending}"
        
        # Test 2: write_enrichment sends correct payload
        success = writer.write_enrichment(
            server_id='test-server-001',
            signal_type='supply_chain',
            score=0.85,
            evidence={'suppliers': ['supplier-a', 'supplier-b']}
        )
        assert test_state['write_called'], "Write endpoint was not called"
        assert test_state['write_payload'] is not None, "Write payload was not captured"
        
        # Test 3: Assert POST payload has correct table and rows keys
        payload = test_state['write_payload']
        assert 'table' in payload, "Payload missing 'table' key"
        assert payload['table'] == 'mcp_signal_enrichments', f"Wrong table: {payload['table']}"
        assert 'rows' in payload, "Payload missing 'rows' key"
        assert isinstance(payload['rows'], list), "rows should be a list"
        assert len(payload['rows']) == 1, f"Expected 1 row, got {len(payload['rows'])}"
        
        row = payload['rows'][0]
        assert 'server_id' in row, "Row missing server_id"
        assert 'signal_type' in row, "Row missing signal_type"
        assert 'score' in row, "Row missing score"
        assert 'evidence_blob' in row, "Row missing evidence_blob"
        assert 'computed_at' in row, "Row missing computed_at"
        
        # Verify data integrity
        assert row['server_id'] == 'test-server-001'
        assert row['signal_type'] == 'supply_chain'
        assert row['score'] == 0.85
        
        # Verify evidence_blob is JSON string
        evidence = json.loads(row['evidence_blob'])
        assert evidence['suppliers'] == ['supplier-a', 'supplier-b']
        
        print("PASS")
        sys.exit(0)
        
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    finally:
        mock_server.shutdown()