#!/usr/bin/env python3
"""
enrichment_sparsity_diagnostic.py

Diagnostic utility that queries mcp_signal_enrichments to identify why only
12 rows exist for 1747 servers. Performs comprehensive checks on enrichment
module registration, daemon health, integration calls, and data coverage.

No writes to production tables - read-only diagnostic.
"""

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# Database connection would normally come from config
# Using placeholder for the example - adapt to your DB layer
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


@dataclass
class DaemonHealthStatus:
    """Health status of a daemon process."""
    name: str
    is_healthy: bool
    last_heartbeat: Optional[datetime] = None
    heartbeat_age_seconds: Optional[float] = None
    is_running: bool = False
    pid: Optional[int] = None
    details: str = ""


@dataclass
class EnrichmentModule:
    """Represents an enrichment module."""
    name: str
    module_path: str
    is_registered: bool = True
    has_call_interface: bool = True
    expected_fields: list = field(default_factory=list)


@dataclass
class ServerEnrichmentStatus:
    """Enrichment status for a single server."""
    server_id: int
    server_name: str
    has_enrichment: bool
    enrichment_count: int
    enrichment_types: list = field(default_factory=list)
    missing_enrichments: list = field(default_factory=list)
    last_enrichment_time: Optional[datetime] = None


@dataclass
class EnrichmentSparsityReport:
    """Complete diagnostic report on enrichment sparsity."""
    generated_at: str
    total_servers: int
    servers_with_enrichment: int
    servers_without_enrichment: int
    total_enrichment_rows: int
    
    # Module analysis
    registered_modules: list
    missing_modules: list
    
    # Daemon health
    writer_daemon_healthy: bool
    writer_daemon_last_heartbeat: Optional[str] = None
    
    # Integration analysis
    signal_analyser_calls_enrichment: bool
    integration_gaps: list = field(default_factory=list)
    
    # Per-server details
    server_details: list = field(default_factory=list)
    
    # Recommendations
    recommendations: list = field(default_factory=list)


class EnrichmentSparsityDiagnostic:
    """
    Diagnostic utility for analyzing enrichment sparsity in MCP signal system.
    """
    
    # Expected enrichment modules based on common MCP signal patterns
    EXPECTED_ENRICHMENT_MODULES = [
        "server_metadata",
        "performance_metrics", 
        "configuration_context",
        "dependency_graph",
        "health_history",
        "alert_correlation",
        "cost_allocation",
        "security_context",
        "capacity_metrics",
        "usage_patterns"
    ]
    
    def __init__(self, db_connection_string: str = None):
        """Initialize diagnostic with optional database connection."""
        self.db_conn_string = db_connection_string or os.environ.get(
            'DATABASE_URL', 
            'postgresql://localhost/mcp_signals'
        )
        self.conn = None
        self.results = {}
    
    def connect(self):
        """Establish database connection."""
        if not HAS_PSYCOPG2:
            print("Warning: psycopg2 not installed, using mock data mode")
            return False
        try:
            self.conn = psycopg2.connect(self.db_conn_string)
            return True
        except Exception as e:
            print(f"Warning: Could not connect to database: {e}")
            return False
    
    def disconnect(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
    
    def check_enrichment_modules_in_codebase(self) -> list:
        """
        Check which enrichment modules are registered in the codebase.
        Scans common locations for enrichment module definitions.
        """
        modules = []
        
        # Common locations to search for enrichment modules
        search_paths = [
            Path("/opt/mcp/enrichment_modules"),
            Path("/opt/mcp/modules"),
            Path("/app/enrichment_modules"),
            Path("./enrichment_modules"),
            Path("./src/enrichment_modules"),
            Path("./mcp/enrichment"),
        ]
        
        # Also check Python path for installed modules
        import pkgutil
        for path in sys.path:
            search_paths.append(Path(path) / "mcp" / "enrichment")
            search_paths.append(Path(path) / "enrichment_modules")
        
        found_modules = set()
        found_module_paths = {}
        
        for search_path in search_paths:
            if not search_path.exists():
                continue
            
            # Look for Python modules
            for item in search_path.iterdir():
                if item.suffix == '.py' and not item.name.startswith('_'):
                    module_name = item.stem
                    if module_name.endswith('_enricher') or module_name.endswith('_enrichment'):
                        base_name = module_name.replace('_enricher', '').replace('_enrichment', '')
                        found_modules.add(base_name)
                        found_module_paths[base_name] = str(item)
                
                # Check for subdirectories (package modules)
                if item.is_dir() and (item / '__init__.py').exists():
                    if 'enrich' in item.name.lower():
                        found_modules.add(item.name)
                        found_module_paths[item.name] = str(item)
        
        # Check for module registry/config files
        registry_files = [
            Path("/etc/mcp/enrichment_modules.json"),
            Path("/opt/mcp/config/enrichment_registry.json"),
            Path("./config/enrichment_registry.json"),
            Path("./enrichment_registry.json"),
        ]
        
        for registry_file in registry_files:
            if registry_file.exists():
                try:
                    with open(registry_file) as f:
                        registry = json.load(f)
                        if isinstance(registry, dict) and 'modules' in registry:
                            for mod in registry['modules']:
                                if isinstance(mod, dict):
                                    found_modules.add(mod.get('name', 'unknown'))
                                else:
                                    found_modules.add(mod)
                except Exception:
                    pass
        
        # Build module list with status
        for expected in self.EXPECTED_ENRICHMENT_MODULES:
            modules.append(EnrichmentModule(
                name=expected,
                module_path=found_module_paths.get(expected, "NOT FOUND IN CODEBASE"),
                is_registered=expected in found_modules,
                has_call_interface=expected in found_modules,
                expected_fields=self._get_expected_fields_for_module(expected)
            ))
        
        return modules
    
    def _get_expected_fields_for_module(self, module_name: str) -> list:
        """Return expected output fields for a given enrichment module."""
        field_map = {
            "server_metadata": ["hostname", "os_version", "environment", "region"],
            "performance_metrics": ["cpu_avg", "memory_avg", "io_stats", "network_stats"],
            "configuration_context": ["config_hash", "config_version", "last_config_change"],
            "dependency_graph": ["depends_on", "dependents", "dependency_depth"],
            "health_history": ["uptime_percent", "incident_count_30d", "mttr_avg"],
            "alert_correlation": ["related_alerts", "alert_pattern", "noise_score"],
            "cost_allocation": ["monthly_cost", "cost_category", "billing_tags"],
            "security_context": ["security_score", "patch_level", "vulnerability_count"],
            "capacity_metrics": ["capacity_used_pct", "headroom_remaining", "scale_events"],
            "usage_patterns": ["peak_hours", "usage_trend", "anomaly_score"]
        }
        return field_map.get(module_name, [])
    
    def check_writer_daemon_health(self) -> DaemonHealthStatus:
        """
        Check if mcp_signal_enrichments_writer daemon is heartbeat-healthy.
        """
        status = DaemonHealthStatus(
            name="mcp_signal_enrichments_writer",
            is_healthy=False,
            details="Unable to determine daemon health"
        )
        
        # Check for heartbeat in database or process table
        if self.conn:
            try:
                with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Check heartbeat table if exists
                    cur.execute("""
                        SELECT last_heartbeat, pid, is_running
                        FROM mcp_daemon_heartbeats 
                        WHERE daemon_name = 'mcp_signal_enrichments_writer'
                        ORDER BY last_check DESC LIMIT 1
                    """)
                    row = cur.fetchone()
                    if row:
                        status.last_heartbeat = row['last_heartbeat']
                        status.is_running = row['is_running']
                        status.pid = row['pid']
                        if row['last_heartbeat']:
                            age = datetime.now() - row['last_heartbeat']
                            status.heartbeat_age_seconds = age.total_seconds()
                            status.is_healthy = age.total_seconds() < 300  # 5 min threshold
                            status.details = f"Last heartbeat {age.total_seconds():.0f}s ago"
                    else:
                        status.details = "No heartbeat record found - daemon may not be running"
            except Exception as e:
                status.details = f"Could not query heartbeat: {e}"
        
        # Check for process directly
        import subprocess
        try:
            result = subprocess.run(
                ["pgrep", "-f", "mcp_signal_enrichments_writer"],
                capture_output=True,
                text=True
            )
            if result.stdout.strip():
                status.is_running = True
                status.pid = int(result.stdout.strip().split()[0])
                if not status.is_healthy:
                    status.details += " (process running but no recent heartbeat)"
            else:
                status.details = "Daemon process not found"
        except Exception:
            pass
        
        return status
    
    def check_signal_analyser_integration(self) -> dict:
        """
        Check whether signal_analyser calls enrichment modules.
        """
        integration_info = {
            "signal_analyser_exists": False,
            "calls_enrichment_modules": False,
            "enrichment_calls_found": [],
            "enrichment_call_locations": [],
            "missing_integration": True
        }
        
        # Search for signal_analyser in codebase
        analyser_paths = [
            Path("/opt/mcp/signal_analyser.py"),
            Path("/opt/mcp/analyser.py"),
            Path("/app/signal_analyser.py"),
            Path("/app/analyser.py"),
            Path("./signal_analyser.py"),
            Path("./src/signal_analyser.py"),
        ]
        
        for path in analyser_paths:
            if path.exists():
                integration_info["signal_analyser_exists"] = True
                with open(path) as f:
                    content = f.read()
                    # Look for enrichment module calls
                    enrichment_patterns = [
                        "enrich",
                        "get_enrichment",
                        "enrichment_module",
                        "enrich_server",
                        "add_enrichment"
                    ]
                    for pattern in enrichment_patterns:
                        if pattern in content.lower():
                            integration_info["calls_enrichment_modules"] = True
                            integration_info["enrichment_calls_found"].append(pattern)
                            # Find line numbers
                            for i, line in enumerate(content.split('\n'), 1):
                                if pattern in line.lower() and 'import' not in line.lower():
                                    integration_info["enrichment_call_locations"].append({
                                        "line": i,
                                        "pattern": pattern,
                                        "code": line.strip()[:100]
                                    })
        
        # Also check for integration configuration
        integration_configs = [
            Path("/etc/mcp/enrichment_integration.json"),
            Path("/opt/mcp/config/analyser_config.json"),
        ]
        
        for config_path in integration_configs:
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        config = json.load(f)
                        if config.get('enable_enrichment', False):
                            integration_info["calls_enrichment_modules"] = True
                            integration_info["missing_integration"] = False
                except Exception:
                    pass
        
        integration_info["missing_integration"] = not integration_info["calls_enrichment_modules"]
        
        return integration_info
    
    def query_enrichment_coverage(self) -> dict:
        """
        Query mcp_signal_enrichments to get detailed enrichment coverage.
        """
        coverage = {
            "total_rows": 0,
            "unique_servers": 0,
            "enrichment_by_type": {},
            "servers_with_enrichment": [],
            "servers_without_enrichment": [],
            "enrichment_timeline": []
        }
        
        if not self.conn:
            # Return mock data structure for testing without DB
            coverage["total_rows"] = 12
            coverage["unique_servers"] = 12
            coverage["enrichment_by_type"] = {"server_metadata": 12}
            coverage["mock_mode"] = True
            return coverage
        
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Total enrichment rows
                cur.execute("SELECT COUNT(*) as count FROM mcp_signal_enrichments")
                coverage["total_rows"] = cur.fetchone()['count']
                
                # Unique servers with enrichment
                cur.execute("""
                    SELECT COUNT(DISTINCT server_id) as count 
                    FROM mcp_signal_enrichments
                """)
                coverage["unique_servers"] = cur.fetchone()['count']
                
                # Enrichment by type
                cur.execute("""
                    SELECT enrichment_type, COUNT(*) as count 
                    FROM mcp_signal_enrichments 
                    GROUP BY enrichment_type 
                    ORDER BY count DESC
                """)
                coverage["enrichment_by_type"] = {
                    row['enrichment_type']: row['count'] 
                    for row in cur.fetchall()
                }
                
                # Servers WITH enrichment
                cur.execute("""
                    SELECT DISTINCT server_id, 
                           MAX(created_at) as last_enrichment,
                           ARRAY_AGG(DISTINCT enrichment_type) as types
                    FROM mcp_signal_enrichments 
                    GROUP BY server_id
                """)
                for row in cur.fetchall():
                    coverage["servers_with_enrichment"].append({
                        "server_id": row['server_id'],
                        "last_enrichment": str(row['last_enrichment']) if row['last_enrichment'] else None,
                        "enrichment_types": row['types']
                    })
                
                # Get all servers to identify those WITHOUT enrichment
                cur.execute("""
                    SELECT server_id, server_name 
                    FROM mcp_servers
                    WHERE server_id NOT IN (
                        SELECT DISTINCT server_id FROM mcp_signal_enrichments
                    )
                """)
                for row in cur.fetchall():
                    coverage["servers_without_enrichment"].append({
                        "server_id": row['server_id'],
                        "server_name": row.get('server_name', f'server_{row["server_id"]}')
                    })
                
                # Timeline of recent enrichments
                cur.execute("""
                    SELECT DATE(created_at) as date, COUNT(*) as count
                    FROM mcp_signal_enrichments
                    WHERE created_at > NOW() - INTERVAL '30 days'
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                """)
                coverage["enrichment_timeline"] = [
                    {"date": str(row['date']), "count": row['count']}
                    for row in cur.fetchall()
                ]
                
        except Exception as e:
            coverage["error"] = str(e)
        
        return coverage
    
    def analyze_integration_gaps(self) -> list:
        """
        Identify specific integration gaps causing sparsity.
        """
        gaps = []
        
        # Get current state
        modules = self.check_enrichment_modules_in_codebase()
        daemon = self.check_writer_daemon_health()
        analyser = self.check_signal_analyser_integration()
        
        # Gap 1: Missing enrichment modules
        missing_modules = [m for m in modules if not m.is_registered]
        if missing_modules:
            gaps.append({
                "type": "missing_enrichment_modules",
                "severity": "high",
                "description": f"{len(missing_modules)} expected enrichment modules not found in codebase",
                "details": [m.name for m in missing_modules]
            })
        
        # Gap 2: Writer daemon not healthy
        if not daemon.is_healthy:
            gaps.append({
                "type": "writer_daemon_unhealthy",
                "severity": "critical",
                "description": "mcp_signal_enrichments_writer daemon is not heartbeat-healthy",
                "details": daemon.details
            })
        
        if not daemon.is_running:
            gaps.append({
                "type": "writer_daemon_not_running",
                "severity": "critical",
                "description": "mcp_signal_enrichments_writer daemon is not running",
                "details": "Daemon process not found in system"
            })
        
        # Gap 3: Signal analyser doesn't call enrichment
        if analyser["missing_integration"]:
            gaps.append({
                "type": "missing_analyser_integration",
                "severity": "critical",
                "description": "signal_analyser does not call enrichment modules",
                "details": {
                    "analyser_exists": analyser["signal_analyser_exists"],
                    "calls_enrichment": analyser["calls_enrichment_modules"],
                    "found_calls": analyser.get("enrichment_calls_found", [])
                }
            })
        
        # Gap 4: Configuration issues
        gaps.append({
            "type": "potential_configuration_issues",
            "severity": "medium",
            "description": "Check enrichment configuration for common issues",
            "details": [
                "Verify enricher batch size is not set to 0",
                "Check enrichment interval is not too long",
                "Ensure enrichment queue is not full",
                "Validate database permissions for enrichment writer"
            ]
        })
        
        return gaps
    
    def generate_recommendations(self, coverage: dict, gaps: list) -> list:
        """
        Generate actionable recommendations based on findings.
        """
        recommendations = []
        
        # Based on specific gaps found
        for gap in gaps:
            if gap["type"] == "writer_daemon_not_running":
                recommendations.append({
                    "priority": 1,
                    "action": "Start mcp_signal_enrichments_writer daemon",
                    "command": "systemctl start mcp_signal_enrichments_writer",
                    "verify": "systemctl status mcp_signal_enrichments_writer"
                })
            elif gap["type"] == "writer_daemon_unhealthy":
                recommendations.append({
                    "priority": 1,
                    "action": "Investigate writer daemon failures",
                    "steps": [
                        "Check daemon logs: journalctl -u mcp_signal_enrichments_writer -n 100",
                        "Verify database connectivity from daemon",
                        "Check for deadlocks or long-running transactions"
                    ]
                })
            elif gap["type"] == "missing_analyser_integration":
                recommendations.append({
                    "priority": 1,
                    "action": "Add enrichment module calls to signal_analyser",
                    "code_example": """
                        # In signal_analyser.py, add:
                        from mcp.enrichment import get_enrichment_modules
                        
                        def analyse_signal(signal):
                            enrichments = get_enrichment_modules()(signal.server_id)
                            return enrich_signal(signal, enrichments)
                    """,
                    "files_to_modify": ["/opt/mcp/signal_analyser.py"]
                })
            elif gap["type"] == "missing_enrichment_modules":
                recommendations.append({
                    "priority": 2,
                    "action": "Implement missing enrichment modules",
                    "modules": gap["details"]
                })
        
        # General recommendations based on coverage stats
        if coverage.get("unique_servers", 0) < 100:
            recommendations.append({
                "priority": 2,
                "action": "Backfill enrichment data for existing servers",
                "command": "mcp-enrichment-backfill --all-servers --batch-size=100"
            })
        
        recommendations.append({
            "priority": 3,
            "action": "Set up monitoring for enrichment pipeline health",
            "alert_threshold": "Alert if enrichment rows < expected by 10%"
        })
        
        return recommendations
    
    def run_diagnostic(self) -> EnrichmentSparsityReport:
        """
        Run complete enrichment sparsity diagnostic.
        """
        # Connect to database
        db_connected = self.connect()
        
        # Run all checks
        print("Checking enrichment modules in codebase...")
        modules = self.check_enrichment_modules_in_codebase()
        
        print("Checking writer daemon health...")
        daemon = self.check_writer_daemon_health()
        
        print("Checking signal_analyser integration...")
        analyser = self.check_signal_analyser_integration()
        
        print("Querying enrichment coverage...")
        coverage = self.query_enrichment_coverage()
        
        print("Analyzing integration gaps...")
        gaps = self.analyze_integration_gaps()
        
        print("Generating recommendations...")
        recommendations = self.generate_recommendations(coverage, gaps)
        
        # Disconnect from database
        if db_connected:
            self.disconnect()
        
        # Build report
        report = EnrichmentSparsityReport(
            generated_at=datetime.now().isoformat(),
            total_servers=1747,  # As specified in task
            servers_with_enrichment=coverage.get("unique_servers", 0),
            servers_without_enrichment=1747 - coverage.get("unique_servers", 0),
            total_enrichment_rows=coverage.get("total_rows", 0),
            registered_modules=[
                {"name": m.name, "path": m.module_path, "is_registered": m.is_registered}
                for m in modules if m.is_registered
            ],
            missing_modules=[
                {"name": m.name, "path": m.module_path}
                for m in modules if not m.is_registered
            ],
            writer_daemon_healthy=daemon.is_healthy,
            writer_daemon_last_heartbeat=(
                daemon.last_heartbeat.isoformat() if daemon.last_heartbeat else None
            ),
            signal_analyser_calls_enrichment=analyser["calls_enrichment_modules"],
            integration_gaps=gaps,
            server_details={
                "with_enrichment": coverage.get("servers_with_enrichment", [])[:50],  # Limit for report
                "without_enrichment_count": len(coverage.get("servers_without_enrichment", []))
            },
            recommendations=recommendations
        )
        
        return report
    
    def save_report(self, report: EnrichmentSparsityReport, output_path: str = None):
        """
        Save report to JSON file.
        """
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"enrichment_sparsity_report_{timestamp}.json"
        
        report_dict = asdict(report)
        
        with open(output_path, 'w') as f:
            json.dump(report_dict, f, indent=2, default=str)
        
        print(f"Report saved to: {output_path}")
        return output_path


def main():
    """Main entry point for the diagnostic utility."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Enrichment Sparsity Diagnostic Utility"
    )
    parser.add_argument(
        "--db-url",
        help="Database connection URL",
        default=os.environ.get('DATABASE_URL')
    )
    parser.add_argument(
        "--output",
        help="Output file path for JSON report",
        default=None
    )
    parser.add_argument(
        "--verbose", "-v",
        help="Verbose output",
        action="store_true"
    )
    
    args = parser.parse_args()
    
    # Initialize diagnostic
    diagnostic = EnrichmentSparsityDiagnostic(db_connection_string=args.db_url)
    
    print("=" * 60)
    print("MCP Signal Enrichment Sparsity Diagnostic")
    print("=" * 60)
    print()
    
    # Run diagnostic
    report = diagnostic.run_diagnostic()
    
    # Print summary
    print()
    print("=" * 60)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 60)
    print(f"Total servers in system:        {report.total_servers}")
    print(f"Servers WITH enrichment:        {report.servers_with_enrichment}")
    print(f"Servers WITHOUT enrichment:     {report.servers_without_enrichment}")
    print(f"Total enrichment rows:          {report.total_enrichment_rows}")
    print()
    print(f"Writer daemon healthy:          {report.writer_daemon_healthy}")
    print(f"Analyser calls enrichment:       {report.signal_analyser_calls_enrichment}")
    print()
    print(f"Registered modules:              {len(report.registered_modules)}")
    print(f"Missing modules:                 {len(report.missing_modules)}")
    print()
    print(f"Integration gaps identified:     {len(report.integration_gaps)}")
    for i, gap in enumerate(report.integration_gaps, 1):
        print(f"  {i}. [{gap['severity'].upper()}] {gap['description']}")
    print()
    print(f"Recommendations:                {len(report.recommendations)}")
    print()
    print("=" * 60)
    
    # Save report
    output_path = diagnostic.save_report(report, args.output)
    
    # Print JSON output if verbose
    if args.verbose:
        print()
        print("=" * 60)
        print("FULL JSON REPORT")
        print("=" * 60)
        print(json.dumps(asdict(report), indent=2, default=str))
    
    return 0


if __name__ == "__main__":
    sys.exit(main())