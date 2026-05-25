"""
ZoSentinel Enrichers - Redesigned for Discriminative Power

These enrichers analyze MCP servers to compute:
1. tool_description_safety - evaluates danger potential from tool names and descriptions
2. temporal_stability - evaluates maintenance/reliability from metadata

Both are designed for meaningful score spread and discrimination.
"""

import re
from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass


# =============================================================================
# ENRICHER 1: Tool Description Safety
# =============================================================================

# Compiled dangerous patterns for tool name matching
DANGEROUS_TOOL_PATTERNS = [
    (re.compile(r'exec', re.I), 15, "exec pattern in tool name"),
    (re.compile(r'shell', re.I), 15, "shell pattern in tool name"),
    (re.compile(r'sudo', re.I), 20, "sudo pattern in tool name"),
    (re.compile(r'rm\s+-rf', re.I), 25, "rm -rf destructive pattern"),
    (re.compile(r'\bdel(ete)?\s+-', re.I), 15, "recursive delete pattern"),
    (re.compile(r'\bdrop\s+table\b', re.I), 20, "drop table dangerous pattern"),
    (re.compile(r'truncate', re.I), 15, "truncate destructive pattern"),
    (re.compile(r'spawn', re.I), 10, "spawn pattern"),
    (re.compile(r'fork', re.I), 10, "fork pattern"),
    (re.compile(r'eval', re.I), 15, "eval dangerous pattern"),
    (re.compile(r'runtime', re.I), 5, "runtime execution pattern"),
    (re.compile(r'proxy', re.I), 10, "proxy pattern"),
    (re.compile(r'tunnel', re.I), 10, "tunnel pattern"),
    (re.compile(r'bridge', re.I), 8, "bridge pattern"),
    (re.compile(r'kill', re.I), 15, "kill pattern"),
    (re.compile(r'terminate', re.I), 10, "terminate pattern"),
    (re.compile(r'shutdown', re.I), 15, "shutdown pattern"),
    (re.compile(r'reboot', re.I), 15, "reboot pattern"),
    (re.compile(r'path.?traversal', re.I), 25, "path traversal vulnerability"),
    (re.compile(r'arbitrary', re.I), 15, "arbitrary action pattern"),
    (re.compile(r'raw\s+sql\b', re.I), 20, "raw SQL execution pattern"),
    (re.compile(r'command\s+injection', re.I), 25, "command injection pattern"),
    (re.compile(r'system\s+call', re.I), 15, "system call pattern"),
]

# Patterns for capability escalation detection
CAPABILITY_ESCALATION_PATTERNS = [
    (re.compile(r'read\s+any', re.I), "reads any file"),
    (re.compile(r'write\s+any', re.I), "writes any file"),
    (re.compile(r'delete\s+any', re.I), "deletes any resource"),
    (re.compile(r'exec(ute)?\s+any', re.I), "executes arbitrary code"),
    (re.compile(r'bypass', re.I), "bypasses security controls"),
    (re.compile(r'privilege', re.I), "modifies privileges"),
    (re.compile(r'root\s+access', re.I), "requires/exposes root access"),
    (re.compile(r'admin\s+rights', re.I), "requires admin rights"),
    (re.compile(r'sudo\b', re.I), "elevated privileges"),
    (re.compile(r'full\s+control', re.I), "full control claim"),
    (re.compile(r'unrestricted', re.I), "unrestricted access"),
]


def _analyze_tool_name(tool_name: str) -> Tuple[int, List[str]]:
    """
    Analyze tool name for dangerous patterns.
    Returns (penalty_score, list of matched evidence).
    """
    total_penalty = 0
    evidence = []
    
    if not tool_name:
        return 0, ["empty tool name"]
    
    for pattern, penalty, description in DANGEROUS_TOOL_PATTERNS:
        if pattern.search(tool_name):
            total_penalty += penalty
            evidence.append(description)
    
    return total_penalty, evidence


def _analyze_description(description: str) -> Tuple[int, List[str]]:
    """
    Analyze tool description for quality and warning signs.
    Returns (score adjustment, list of evidence).
    """
    adjustment = 0
    evidence = []
    
    # Empty description
    if not description or not description.strip():
        adjustment -= 20
        evidence.append("empty description (-20)")
        return adjustment, evidence
    
    word_count = len(description.split())
    
    # Vague description (< 10 words)
    if word_count < 10:
        adjustment -= 10
        evidence.append(f"vague description ({word_count} words) (-10)")
    
    # Very short description
    if word_count < 5:
        adjustment -= 5
        evidence.append(f"very short description ({word_count} words) (-5)")
    
    # Check for capability escalation in description
    for pattern, description_text in CAPABILITY_ESCALATION_PATTERNS:
        if pattern.search(description):
            adjustment -= 10
            evidence.append(f"capability escalation signal: {description_text} (-10)")
            break  # Cap escalation penalties
    
    # Positive indicators (good descriptions)
    if word_count >= 30:
        adjustment += 5
        evidence.append("detailed description (+5)")
    
    # Check for safety disclaimers or clear purpose statements
    if any(word in description.lower() for word in ['safe', 'validated', 'sandboxed']):
        adjustment += 5
        evidence.append("safety-related keywords present (+5)")
    
    return adjustment, evidence


def compute_tool_description_safety(server: dict) -> dict:
    """
    Compute tool description safety score for an MCP server.
    
    Score range: 0-100 (lower = more dangerous)
    
    Args:
        server: Dict containing server metadata with at minimum:
            - tool_name: str (optional)
            - description: str (optional)
            - capabilities: list of str (optional)
    
    Returns:
        dict with signal_name, score, evidence, and sub_scores
    """
    base_score = 100
    
    # Gather tool information
    tool_name = server.get('tool_name', '') or server.get('name', '')
    description = server.get('description', '') or ''
    
    # Analyze tool name
    name_penalty, name_evidence = _analyze_tool_name(tool_name)
    
    # Analyze description
    desc_adjustment, desc_evidence = _analyze_description(description)
    
    # Analyze capabilities list if present
    capabilities = server.get('capabilities', [])
    cap_penalty = 0
    cap_evidence = []
    if capabilities:
        cap_text = ' '.join(str(c) for c in capabilities)
        for pattern, desc in CAPABILITY_ESCALATION_PATTERNS:
            if pattern.search(cap_text):
                cap_penalty += 8
                cap_evidence.append(f"escalation in capabilities: {desc}")
    
    # Calculate final score
    total_deductions = name_penalty + abs(desc_adjustment) + cap_penalty
    final_score = max(0, min(100, base_score - total_deductions))
    
    # Compile evidence
    evidence_parts = []
    if name_evidence:
        evidence_parts.extend([f"tool_name: {e}" for e in name_evidence])
    evidence_parts.extend(desc_evidence)
    evidence_parts.extend([f"capability: {e}" for e in cap_evidence])
    
    if not evidence_parts:
        evidence_parts = ["no danger signals detected"]
    
    return {
        'signal_name': 'tool_description_safety',
        'score': final_score,
        'evidence': evidence_parts,
        'sub_scores': {
            'name_analysis': {
                'penalty': name_penalty,
                'matches': name_evidence
            },
            'description_analysis': {
                'adjustment': desc_adjustment,
                'details': desc_evidence
            },
            'capability_analysis': {
                'penalty': cap_penalty,
                'issues': cap_evidence
            }
        }
    }


# =============================================================================
# ENRICHER 2: Temporal Stability
# =============================================================================

# Semver regex pattern
SEMVER_PATTERN = re.compile(
    r'^'
    r'(?P<major>0|[1-9]\d*)\.'
    r'(?P<minor>0|[1-9]\d*)\.'
    r'(?P<patch>0|[1-9]\d*)'
    r'(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)'
    r'(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?'
    r'(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$'
)


def _parse_semver(version: str) -> Tuple[bool, dict]:
    """Parse and validate semver string."""
    if not version:
        return False, {}
    match = SEMVER_PATTERN.match(version.strip())
    if match:
        return True, match.groupdict()
    return False, {}


def _calculate_staleness(date_str: str, reference_date: datetime = None) -> Tuple[int, int, str]:
    """
    Calculate staleness penalty based on last update date.
    Returns (penalty, days_since, description).
    """
    if not reference_date:
        reference_date = datetime.now()
    
    if not date_str:
        return -15, 0, "no update date provided"
    
    try:
        # Try parsing various date formats
        date_formats = [
            '%Y-%m-%d',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S.%fZ',
            '%Y/%m/%d',
        ]
        
        update_date = None
        for fmt in date_formats:
            try:
                update_date = datetime.strptime(date_str.strip(), fmt)
                break
            except ValueError:
                continue
        
        if update_date is None:
            # Try ISO format
            try:
                update_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return -10, 0, f"unparseable date: {date_str}"
        
        days_since = (reference_date - update_date).days
        
        # Staleness penalties
        if days_since < 0:
            return 0, days_since, "future date (suspicious)"
        elif days_since == 0:
            return 0, 0, "updated today"
        elif days_since <= 30:
            return 0, days_since, f"recently updated ({days_since} days ago)"
        elif days_since <= 90:
            return -5, days_since, f"moderately recent ({days_since} days ago)"
        elif days_since <= 180:
            return -10, days_since, f"getting stale ({days_since} days ago)"
        elif days_since <= 365:
            return -20, days_since, f"stale ({days_since} days ago)"
        elif days_since <= 730:
            return -30, days_since, f"very stale ({days_since} days ago)"
        else:
            return -40, days_since, f"ancient ({days_since} days ago)"
            
    except Exception as e:
        return -10, 0, f"date parsing error: {str(e)}"


def _analyze_version(version: str) -> Tuple[int, str]:
    """Analyze version string for validity."""
    if not version:
        return -5, "no version specified"
    
    is_valid, parsed = _parse_semver(version)
    if is_valid:
        major = int(parsed.get('major', 0))
        if major == 0:
            return -5, f"pre-1.0 version: {version}"
        return 10, f"valid semver: {version}"
    else:
        # Check for partial versioning
        if re.match(r'^\d+\.\d+', version):
            return 2, f"partial version: {version}"
        return -5, f"invalid version format: {version}"


def _analyze_scans(scan_count: int) -> Tuple[int, str]:
    """Analyze security scan count."""
    if scan_count is None or scan_count < 0:
        return 0, "unknown scan history"
    
    if scan_count == 0:
        return -15, "never scanned"
    elif scan_count == 1:
        return -5, "scanned once"
    elif scan_count <= 3:
        return 0, f"scanned {scan_count} times"
    elif scan_count <= 5:
        return 5, f"well-scanned ({scan_count} times)"
    else:
        return 10, f"heavily verified ({scan_count} scans)"


def compute_temporal_stability(server: dict, reference_date: datetime = None) -> dict:
    """
    Compute temporal stability score for an MCP server.
    
    Score range: 0-100 (higher = more stable/maintained)
    
    Args:
        server: Dict containing server metadata with optional:
            - metadata.date: str (last update date)
            - metadata.version: str (version string)
            - registry.scan_count: int (number of security scans)
            - date: str (alternative date field)
            - version: str (alternative version field)
    
    Returns:
        dict with signal_name, score, evidence, and sub_scores
    """
    base_score = 100
    
    # Extract metadata with fallbacks
    metadata = server.get('metadata', {})
    registry = server.get('registry', {})
    
    date_str = (metadata.get('date') or 
                metadata.get('last_updated') or 
                server.get('date') or
                server.get('last_update', ''))
    
    version_str = (metadata.get('version') or 
                   server.get('version', ''))
    
    scan_count = (registry.get('scan_count') or 
                  metadata.get('scan_count') or 
                  server.get('scan_count', 0))
    
    # Calculate sub-scores
    staleness_penalty, days_since, staleness_evidence = _calculate_staleness(
        date_str, reference_date
    )
    
    version_score, version_evidence = _analyze_version(version_str)
    
    scan_score, scan_evidence = _analyze_scans(scan_count)
    
    # Calculate final score
    total_adjustment = staleness_penalty + version_score + scan_score
    final_score = max(0, min(100, base_score + total_adjustment))
    
    # Compile evidence
    evidence_parts = [
        f"staleness: {staleness_evidence}",
        f"version: {version_evidence}",
        f"scans: {scan_evidence}"
    ]
    
    return {
        'signal_name': 'temporal_stability',
        'score': final_score,
        'evidence': evidence_parts,
        'sub_scores': {
            'staleness': {
                'penalty': staleness_penalty,
                'days_since_update': days_since,
                'description': staleness_evidence
            },
            'version': {
                'score': version_score,
                'description': version_evidence
            },
            'scan_history': {
                'score': scan_score,
                'scan_count': scan_count,
                'description': scan_evidence
            }
        }
    }


# =============================================================================
# UNIT TESTS
# =============================================================================

import unittest


class TestToolDescriptionSafety(unittest.TestCase):
    """Unit tests for compute_tool_description_safety enricher."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.results = []
    
    def tearDown(self):
        """Verify discrimination across test cases."""
        if self.results:
            scores = [r['score'] for r in self.results]
            unique_scores = len(set(scores))
            # This verifies we have meaningful discrimination
            self.assertGreaterEqual(unique_scores, 6, 
                f"Only {unique_scores} distinct scores, need 6+ for discrimination")
    
    def test_empty_server(self):
        """Test server with minimal data."""
        server = {}
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertEqual(result['signal_name'], 'tool_description_safety')
        self.assertGreaterEqual(result['score'], 80)  # Should be high (safe)
        self.assertIn('evidence', result)
        print(f"Empty server: score={result['score']}")
    
    def test_safe_tool(self):
        """Test safe tool with good description."""
        server = {
            'tool_name': 'calculate_sum',
            'description': 'A safe mathematical function that computes the sum of two numbers.',
            'capabilities': ['read numbers', 'compute addition']
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertGreaterEqual(result['score'], 85)
        self.assertIn('no danger signals detected', result['evidence'])
        print(f"Safe tool: score={result['score']}")
    
    def test_dangerous_exec_tool(self):
        """Test dangerous exec tool."""
        server = {
            'tool_name': 'shell_exec',
            'description': 'Execute arbitrary shell commands on the system.'
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertLess(result['score'], 60)
        self.assertTrue(any('shell' in e for e in result['evidence']))
        print(f"Dangerous exec: score={result['score']}")
    
    def test_dangerous_delete_tool(self):
        """Test dangerous delete/rmrf tool."""
        server = {
            'tool_name': 'recursive_delete_all',
            'description': 'Delete files and directories recursively without confirmation.'
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertLess(result['score'], 50)
        print(f"Dangerous delete: score={result['score']}")
    
    def test_path_traversal_vulnerability(self):
        """Test tool with path traversal vulnerability."""
        server = {
            'tool_name': 'read_file_path_traversal',
            'description': 'Read any file from the filesystem using path traversal.'
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertLess(result['score'], 40)
        print(f"Path traversal: score={result['score']}")
    
    def test_vague_description(self):
        """Test tool with vague/empty description."""
        server = {
            'tool_name': 'do_something',
            'description': 'Does stuff.'
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertLess(result['score'], 90)
        self.assertTrue(any('vague' in e.lower() for e in result['evidence']))
        print(f"Vague description: score={result['score']}")
    
    def test_empty_description(self):
        """Test tool with completely empty description."""
        server = {
            'tool_name': 'critical_tool',
            'description': ''
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertLess(result['score'], 80)
        self.assertTrue(any('empty' in e.lower() for e in result['evidence']))
        print(f"Empty description: score={result['score']}")
    
    def test_capability_escalation(self):
        """Test tool with capability escalation signals."""
        server = {
            'tool_name': 'file_manager',
            'description': 'Can read any file on the system and bypass security controls.',
            'capabilities': ['full control', 'read any file', 'privilege elevation']
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertLess(result['score'], 60)
        print(f"Capability escalation: score={result['score']}")
    
    def test_sudo_pattern(self):
        """Test tool with sudo pattern."""
        server = {
            'tool_name': 'sudo_execute',
            'description': 'Execute commands with elevated sudo privileges.'
        }
        result = compute_tool_description_safety(server)
        self.results.append(result)
        
        self.assertLess(result['score'], 50)
        print(f"Sudo pattern: score={result['score']}")


class TestTemporalStability(unittest.TestCase):
    """Unit tests for compute_temporal_stability enricher."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.reference_date = datetime(2024, 6, 15)
        self.results = []
    
    def tearDown(self):
        """Verify discrimination across test cases."""
        if self.results:
            scores = [r['score'] for r in self.results]
            unique_scores = len(set(scores))
            self.assertGreaterEqual(unique_scores, 8,
                f"Only {unique_scores} distinct scores, need 8+ for discrimination")
    
    def test_recently_updated_with_scans(self):
        """Test well-maintained, recently updated server."""
        server = {
            'metadata': {
                'date': '2024-06-10',
                'version': '2.1.0'
            },
            'registry': {
                'scan_count': 10
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertGreaterEqual(result['score'], 100)
        print(f"Recently updated: score={result['score']}")
    
    def test_stale_server(self):
        """Test stale server (>180 days)."""
        server = {
            'metadata': {
                'date': '2023-10-01'  # ~256 days ago
            },
            'registry': {
                'scan_count': 0
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertLess(result['score'], 75)
        print(f"Stale server: score={result['score']}")
    
    def test_ancient_server(self):
        """Test very old server (>365 days)."""
        server = {
            'metadata': {
                'date': '2022-01-01'
            },
            'registry': {
                'scan_count': 0
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertLess(result['score'], 60)
        print(f"Ancient server: score={result['score']}")
    
    def test_no_version(self):
        """Test server with no version specified."""
        server = {
            'metadata': {
                'date': '2024-01-15'
            },
            'registry': {
                'scan_count': 5
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertLess(result['score'], 100)
        print(f"No version: score={result['score']}")
    
    def test_invalid_version(self):
        """Test server with invalid version string."""
        server = {
            'metadata': {
                'date': '2024-03-01',
                'version': 'latest'
            },
            'registry': {
                'scan_count': 2
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertLess(result['score'], 100)
        self.assertIn('invalid', result['evidence'][1].lower())
        print(f"Invalid version: score={result['score']}")
    
    def test_valid_semver(self):
        """Test server with valid semver."""
        server = {
            'metadata': {
                'date': '2024-05-01',
                'version': '3.2.1'
            },
            'registry': {
                'scan_count': 8
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertGreaterEqual(result['score'], 100)
        self.assertIn('valid semver', result['evidence'][1].lower())
        print(f"Valid semver: score={result['score']}")
    
    def test_pre_release_version(self):
        """Test server with pre-1.0 version."""
        server = {
            'metadata': {
                'date': '2024-04-01',
                'version': '0.9.5'
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertLess(result['score'], 105)
        print(f"Pre-release: score={result['score']}")
    
    def test_never_scanned(self):
        """Test server that has never been scanned."""
        server = {
            'metadata': {
                'date': '2024-02-01',
                'version': '1.0.0'
            },
            'registry': {
                'scan_count': 0
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertLess(result['score'], 90)
        self.assertIn('never scanned', result['evidence'][2].lower())
        print(f"Never scanned: score={result['score']}")
    
    def test_heavily_verified(self):
        """Test heavily scanned/verified server."""
        server = {
            'metadata': {
                'date': '2024-05-15',
                'version': '2.0.0'
            },
            'registry': {
                'scan_count': 20
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertGreaterEqual(result['score'], 105)
        print(f"Heavily verified: score={result['score']}")
    
    def test_no_date_provided(self):
        """Test server with no date metadata."""
        server = {
            'metadata': {},
            'registry': {
                'scan_count': 3
            }
        }
        result = compute_temporal_stability(server, self.reference_date)
        self.results.append(result)
        
        self.assertLess(result['score'], 90)
        print(f"No date: score={result['score']}")


class TestDiscrimination(unittest.TestCase):
    """Integration tests to verify overall discrimination power."""
    
    def test_tool_safety_discrimination(self):
        """Verify tool_safety produces 8+ distinct scores."""
        test_servers = [
            {'tool_name': 'safe_add', 'description': 'Adds two numbers together safely.'},
            {'tool_name': 'calculate', 'description': 'Performs calculations.'},
            {'tool_name': 'exec', 'description': 'Run shell commands.'},
            {'tool_name': 'shell_tool', 'description': 'Access shell.'},
            {'tool_name': '', 'description': ''},
            {'tool_name': 'delete_all', 'description': 'rm -rf everything'},
            {'tool_name': 'file_read', 'description': 'Read any file path.'},
            {'tool_name': 'path_traverse', 'description': 'Use path traversal.'},
            {'tool_name': 'sudo_exec', 'description': 'Execute with sudo.'},
            {'tool_name': 'sys_exec', 'description': 'Execute system commands.'},
            {'tool_name': 'command_run', 'description': 'Run arbitrary commands.'},
            {'tool_name': 'sql_query', 'description': 'Execute raw SQL queries.'},
        ]
        
        scores = [compute_tool_description_safety(s)['score'] for s in test_servers]
        unique = len(set(scores))
        
        print(f"\nTool Safety Score Distribution:")
        print(f"  Unique scores: {unique}")
        print(f"  Score range: {min(scores)} - {max(scores)}")
        print(f"  Scores: {sorted(set(scores))}")
        
        self.assertGreaterEqual(unique, 8, 
            f"Need 8+ distinct scores, got {unique}")
    
    def test_temporal_stability_discrimination(self):
        """Verify temporal_stability produces 10+ distinct scores."""
        test_servers = [
            {'metadata': {'date': '2024-06-14', 'version': '3.0.0'}, 'registry': {'scan_count': 20}},
            {'metadata': {'date': '2024-06-01', 'version': '2.5.0'}, 'registry': {'scan_count': 15}},
            {'metadata': {'date': '2024-05-01', 'version': '2.0.0'}, 'registry': {'scan_count': 10}},
            {'metadata': {'date': '2024-03-01', 'version': '1.5.0'}, 'registry': {'scan_count': 5}},
            {'metadata': {'date': '2024-01-15', 'version': '1.0.0'}, 'registry': {'scan_count': 3}},
            {'metadata': {'date': '2023-11-01', 'version': '0.9.0'}, 'registry': {'scan_count': 1}},
            {'metadata': {'date': '2023-08-01', 'version': '0.5.0'}, 'registry': {'scan_count': 0}},
            {'metadata': {'date': '2023-01-01', 'version': '0.1.0'}, 'registry': {'scan_count': 0}},
            {'metadata': {'date': '2022-06-01'}, 'registry': {'scan_count': 0}},
            {'metadata': {'version': '1.0.0'}, 'registry': {'scan_count': 10}},
            {'metadata': {'date': '2024-05-01', 'version': 'latest'}, 'registry': {'scan_count': 5}},
            {'metadata': {'date': '2024-04-01', 'version': 'v1'}, 'registry': {'scan_count': 3}},
            {'metadata': {}, 'registry': {'scan_count': 0}},
            {'metadata': {'date': '2024-06-01', 'version': '1.2.3-beta'}},
        ]
        
        ref_date = datetime(2024, 6, 15)
        scores = [compute_temporal_stability(s, ref_date)['score'] for s in test_servers]
        unique = len(set(scores))
        
        print(f"\nTemporal Stability Score Distribution:")
        print(f"  Unique scores: {unique}")
        print(f"  Score range: {min(scores)} - {max(scores)}")
        print(f"  Scores: {sorted(set(scores))}")
        
        self.assertGreaterEqual(unique, 10,
            f"Need 10+ distinct scores, got {unique}")


if __name__ == '__main__':
    # Run with verbose output
    unittest.main(verbosity=2)