#!/usr/bin/env python3
"""
ZO-SENTINEL: Manager Agent Stale Diagnostic Module
Diagnoses why manager_agent is stale at 14 minutes (threshold 300s/5m)
"""

import logging
import os
import sys
import json
import re
import requests
import importlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from enum import Enum


WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
DUCKDB_PATH = "/home/workspace/zo_sentinel/data/zo_sentinel.db"
MANAGER_AGENT_PID_FILE = "/home/workspace/zo_sentinel/pids/manager_agent.pid"
MANAGER_AGENT_SCRIPT = "/home/workspace/zo_sentinel/manager_agent.py"
MANAGER_AGENT_LOG_FILE = "/home/workspace/zo_sentinel/logs/manager_agent.log"
MANAGER_AGENT_LOG_DIR = "/home/workspace/zo_sentinel/logs"
STALE_THRESHOLD_SECONDS = 300
CURRENT_STALENESS_SECONDS = 840


class DiagnosticSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ManagerAgentStaleDiagnostics:
    def __init__(self):
        self.findings = []
        self.diagnostics_output = []
        self.logger = logging.getLogger("zo_sentinel.diagnostics")
        
    def add_finding(self, severity: DiagnosticSeverity, category: str, message: str, details: Optional[Dict] = None):
        finding = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'severity': severity.value,
            'category': category,
            'message': message,
            'details': details or {}
        }
        self.findings.append(finding)
        prefix = f"[{severity.value}] {category}"
        self.diagnostics_output.append(f"{prefix}: {message}")
        if details:
            self.diagnostics_output.append(f"  Details: {json.dumps(details, indent=2)}")
        self.logger.info(f"{prefix}: {message}")
        
    def check_service_health_table(self) -> Dict[str, Any]:
        self.diagnostics_output.append("\n=== CHECK 1: Service Health Table ===")
        self.logger.info("Checking service_health table for manager_agent entry")
        
        result = {
            'table_exists': False,
            'entry_exists': False,
            'last_heartbeat': None,
            'staleness_seconds': None,
            'is_stale': False
        }
        
        try:
            payload = {
                'table': 'service_health',
                'query': "SELECT * FROM service_health WHERE service = 'manager_agent' LIMIT 1",
                'wait': True
            }
            response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
            
            if response.status_code == 200:
                result['table_exists'] = True
                data = response.json()
                
                if data.get('row_count', 0) > 0 and data.get('rows'):
                    result['entry_exists'] = True
                    entry = data['rows'][0]
                    last_heartbeat = entry.get('last_heartbeat')
                    result['last_heartbeat'] = last_heartbeat
                    
                    if last_heartbeat:
                        try:
                            hb_time = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                            now = datetime.now(timezone.utc)
                            staleness = (now - hb_time).total_seconds()
                            result['staleness_seconds'] = staleness
                            result['is_stale'] = staleness > STALE_THRESHOLD_SECONDS
                            
                            self.add_finding(
                                DiagnosticSeverity.INFO,
                                "SERVICE_HEALTH",
                                f"Last heartbeat: {last_heartbeat}",
                                {'staleness_seconds': staleness, 'threshold': STALE_THRESHOLD_SECONDS}
                            )
                            
                            if staleness > CURRENT_STALENESS_SECONDS:
                                self.add_finding(
                                    DiagnosticSeverity.WARNING,
                                    "SERVICE_HEALTH",
                                    f"Heartbeat is {staleness:.0f}s old - significantly exceeds 5m threshold",
                                    {'current_staleness': CURRENT_STALENESS_SECONDS}
                                )
                        except Exception as e:
                            self.add_finding(
                                DiagnosticSeverity.WARNING,
                                "SERVICE_HEALTH",
                                f"Failed to parse heartbeat timestamp: {e}",
                                {'raw_value': last_heartbeat}
                            )
                else:
                    self.add_finding(
                        DiagnosticSeverity.CRITICAL,
                        "SERVICE_HEALTH",
                        "No entry found for manager_agent in service_health table",
                        {'query_result': data}
                    )
            else:
                self.add_finding(
                    DiagnosticSeverity.CRITICAL,
                    "SERVICE_HEALTH",
                    f"Failed to query service_health table: HTTP {response.status_code}",
                    {'response_text': response.text[:500]}
                )
                
        except requests.exceptions.ConnectionError:
            self.add_finding(
                DiagnosticSeverity.CRITICAL,
                "SERVICE_HEALTH",
                "Cannot connect to write_service at 127.0.0.1:8772",
                {'write_service_url': WRITE_SERVICE_URL}
            )
        except Exception as e:
            self.add_finding(
                DiagnosticSeverity.CRITICAL,
                "SERVICE_HEALTH",
                f"Error querying service_health table: {str(e)}",
                {'exception_type': type(e).__name__}
            )
            
        return result
    
    def check_process_status(self) -> Dict[str, Any]:
        self.diagnostics_output.append("\n=== CHECK 2: Process Status via PID File ===")
        self.logger.info("Checking manager_agent process status")
        
        result = {
            'pid_file_exists': False,
            'pid_value': None,
            'process_exists': False,
            'process_info': None,
            'is_zombie': False,
            'is_running': False
        }
        
        if os.path.exists(MANAGER_AGENT_PID_FILE):
            result['pid_file_exists'] = True
            try:
                with open(MANAGER_AGENT_PID_FILE, 'r') as f:
                    pid_str = f.read().strip()
                    pid = int(pid_str)
                    result['pid_value'] = pid
                    
                self.add_finding(
                    DiagnosticSeverity.INFO,
                    "PROCESS_STATUS",
                    f"PID file exists with PID: {pid}",
                    {'pid_file': MANAGER_AGENT_PID_FILE}
                )
                
                try:
                    import psutil
                    if psutil.pid_exists(pid):
                        result['process_exists'] = True
                        process = psutil.Process(pid)
                        result['process_info'] = {
                            'name': process.name(),
                            'status': process.status(),
                            'create_time': process.create_time(),
                            'cmdline': ' '.join(process.cmdline()) if process.cmdline() else None
                        }
                        result['is_running'] = process.status() == psutil.STATUS_RUNNING
                        result['is_zombie'] = process.status() == psutil.STATUS_ZOMBIE
                        
                        status = process.status()
                        self.add_finding(
                            DiagnosticSeverity.INFO,
                            "PROCESS_STATUS",
                            f"Process status: {status}",
                            {
                                'pid': pid,
                                'name': process.name(),
                                'create_time': datetime.fromtimestamp(process.create_time(), tz=timezone.utc).isoformat()
                            }
                        )
                        
                        if status == psutil.STATUS_ZOMBIE:
                            self.add_finding(
                                DiagnosticSeverity.CRITICAL,
                                "PROCESS_STATUS",
                                "Process is in ZOMBIE state - parent process may have crashed",
                                {'pid': pid}
                            )
                        elif status != psutil.STATUS_RUNNING:
                            self.add_finding(
                                DiagnosticSeverity.WARNING,
                                "PROCESS_STATUS",
                                f"Process exists but not in RUNNING state: {status}",
                                {'pid': pid}
                            )
                            
                        cmdline = process.cmdline()
                        if cmdline and any('manager_agent' in str(arg) for arg in cmdline):
                            self.add_finding(
                                DiagnosticSeverity.INFO,
                                "PROCESS_STATUS",
                                "Process command line confirms manager_agent",
                                {'cmdline': cmdline}
                            )
                        else:
                            self.add_finding(
                                DiagnosticSeverity.WARNING,
                                "PROCESS_STATUS",
                                "Process command line does not reference manager_agent",
                                {'cmdline': cmdline}
                            )
                    else:
                        self.add_finding(
                            DiagnosticSeverity.CRITICAL,
                            "PROCESS_STATUS",
                            "PID file exists but process is not running",
                            {'pid': pid, 'pid_file': MANAGER_AGENT_PID_FILE}
                        )
                        
                except ImportError:
                    self.add_finding(
                        DiagnosticSeverity.WARNING,
                        "PROCESS_STATUS",
                        "psutil not available - cannot verify process status",
                        {}
                    )
                    result['process_exists'] = os.path.exists(f'/proc/{pid}')
                    if result['process_exists']:
                        self.add_finding(
                            DiagnosticSeverity.INFO,
                            "PROCESS_STATUS",
                            f"Process {pid} exists (verified via /proc)",
                            {}
                        )
                except Exception as e:
                    self.add_finding(
                        DiagnosticSeverity.WARNING,
                        "PROCESS_STATUS",
                        f"Error checking process: {str(e)}",
                        {'pid': pid}
                    )
                    
            except ValueError:
                self.add_finding(
                    DiagnosticSeverity.CRITICAL,
                    "PROCESS_STATUS",
                    "PID file contains invalid value",
                    {'content': open(MANAGER_AGENT_PID_FILE).read() if os.path.exists(MANAGER_AGENT_PID_FILE) else 'N/A'}
                )
            except Exception as e:
                self.add_finding(
                    DiagnosticSeverity.CRITICAL,
                    "PROCESS_STATUS",
                    f"Error reading PID file: {str(e)}",
                    {}
                )
        else:
            self.add_finding(
                DiagnosticSeverity.CRITICAL,
                "PROCESS_STATUS",
                "PID file does not exist",
                {'pid_file': MANAGER_AGENT_PID_FILE}
            )
            
        return result
    
    def check_log_exceptions(self) -> Dict[str, Any]:
        self.diagnostics_output.append("\n=== CHECK 3: Recent Exceptions in Logs ===")
        self.logger.info("Checking logs for recent exceptions")
        
        result = {
            'log_files_found': [],
            'exceptions_found': [],
            'exception_count': 0,
            'recent_critical': None,
            'recent_warning': None
        }
        
        log_files = []
        if os.path.exists(MANAGER_AGENT_LOG_DIR):
            log_files = list(Path(MANAGER_AGENT_LOG_DIR).glob("manager_agent*.log"))
            result['log_files_found'] = [str(f) for f in log_files]
            
        if not log_files:
            self.add_finding(
                DiagnosticSeverity.WARNING,
                "LOG_EXCEPTIONS",
                "No manager_agent log files found",
                {'log_dir': MANAGER_AGENT_LOG_DIR}
            )
            return result
            
        self.add_finding(
            DiagnosticSeverity.INFO,
            "LOG_EXCEPTIONS",
            f"Found {len(log_files)} log file(s) to analyze",
            {'files': result['log_files_found']}
        )
        
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=20)
        exception_patterns = [
            r'\b(Exception|Error|Traceback|Traceback most recent call last)\b',
            r'\b(CRITICAL|FATAL)\b',
            r'TimeoutError',
            r'ConnectionError',
            r'ConnectionRefusedError'
        ]
        
        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    
                for line in lines:
                    try:
                        line_lower = line.lower()
                        for pattern in exception_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                if 'timestamp' in line.lower() or re.match(r'\d{4}-\d{2}-\d{2}', line):
                                    try:
                                        dt_str = line[:19]
                                        log_time = datetime.fromisoformat(dt_str)
                                        if log_time.tzinfo is None:
                                            log_time = log_time.replace(tzinfo=timezone.utc)
                                        if log_time >= cutoff_time:
                                            result['exceptions_found'].append({
                                                'file': str(log_file.name),
                                                'timestamp': dt_str,
                                                'line': line.strip()
                                            })
                                    except:
                                        result['exceptions_found'].append({
                                            'file': str(log_file.name),
                                            'line': line.strip()
                                        })
                                break
                    except:
                        continue
                        
            except Exception as e:
                self.add_finding(
                    DiagnosticSeverity.WARNING,
                    "LOG_EXCEPTIONS",
                    f"Error reading log file {log_file}: {str(e)}",
                    {}
                )
                
        result['exception_count'] = len(result['exceptions_found'])
        
        if result['exceptions_found']:
            self.add_finding(
                DiagnosticSeverity.WARNING,
                "LOG_EXCEPTIONS",
                f"Found {result['exception_count']} exception(s) in recent logs",
                {'sample': result['exceptions_found'][:5]}
            )
            
            recent = result['exceptions_found'][0]
            self.add_finding(
                DiagnosticSeverity.CRITICAL,
                "LOG_EXCEPTIONS",
                f"Most recent exception: {recent['line'][:100]}",
                {'timestamp': recent.get('timestamp')}
            )
        else:
            self.add_finding(
                DiagnosticSeverity.INFO,
                "LOG_EXCEPTIONS",
                "No exceptions found in recent logs",
                {}
            )
            
        return result
    
    def check_dependency_imports(self) -> Dict[str, Any]:
        self.diagnostics_output.append("\n=== CHECK 4: Dependency Import Check ===")
        self.logger.info("Checking manager_agent dependencies")
        
        result = {
            'script_exists': False,
            'imports_checked': [],
            'import_results': {},
            'failed_imports': []
        }
        
        if os.path.exists(MANAGER_AGENT_SCRIPT):
            result['script_exists'] = True
            self.add_finding(
                DiagnosticSeverity.INFO,
                "DEPENDENCY_IMPORTS",
                f"Manager agent script found at {MANAGER_AGENT_SCRIPT}",
                {}
            )
        else:
            self.add_finding(
                DiagnosticSeverity.CRITICAL,
                "DEPENDENCY_IMPORTS",
                "Manager agent script not found",
                {'expected_path': MANAGER_AGENT_SCRIPT}
            )
            return result
            
        common_dependencies = [
            'fastapi', 'requests', 'duckdb', 'pydantic', 
            'psutil', 'logging', 'datetime', 'pathlib',
            'asyncio', 'concurrent.futures'
        ]
        
        for module_name in common_dependencies:
            result['imports_checked'].append(module_name)
            try:
                importlib.import_module(module_name)
                result['import_results'][module_name] = {'status': 'success'}
            except ImportError as e:
                result['import_results'][module_name] = {'status': 'failed', 'error': str(e)}
                result['failed_imports'].append(module_name)
                self.add_finding(
                    DiagnosticSeverity.CRITICAL,
                    "DEPENDENCY_IMPORTS",
                    f"Failed to import {module_name}: {str(e)}",
                    {}
                )
            except Exception as e:
                result['import_results'][module_name] = {'status': 'error', 'error': str(e)}
                self.add_finding(
                    DiagnosticSeverity.WARNING,
                    "DEPENDENCY_IMPORTS",
                    f"Unexpected error importing {module_name}: {str(e)}",
                    {}
                )
                
        success_count = sum(1 for r in result['import_results'].values() if r['status'] == 'success')
        self.add_finding(
            DiagnosticSeverity.INFO,
            "DEPENDENCY_IMPORTS",
            f"Import check complete: {success_count}/{len(result['imports_checked'])} successful",
            {'results': result['import_results']}
        )
        
        if result['failed_imports']:
            self.add_finding(
                DiagnosticSeverity.CRITICAL,
                "DEPENDENCY_IMPORTS",
                f"Missing dependencies may prevent manager_agent from running: {result['failed_imports']}",
                {'failed': result['failed_imports']}
            )
            
        return result
    
    def compile_diagnostic_report(self) -> str:
        self.diagnostics_output.append("\n" + "="*60)
        self.diagnostics_output.append("DIAGNOSTIC REPORT: Manager Agent Stale Condition")
        self.diagnostics_output.append("="*60)
        
        self.add_finding(
            DiagnosticSeverity.INFO,
            "REPORT_HEADER",
            f"Diagnostics run at {datetime.now(timezone.utc).isoformat()}",
            {
                'current_staleness_seconds': CURRENT_STALENESS_SECONDS,
                'stale_threshold_seconds': STALE_THRESHOLD_SECONDS,
                'threshold_exceeded_by': CURRENT_STALENESS_SECONDS - STALE_THRESHOLD_SECONDS
            }
        )
        
        self.diagnostics_output.append("\n### Actionable Findings ###")
        
        critical_findings = [f for f in self.findings if f['severity'] == 'CRITICAL']
        warning_findings = [f for f in self.findings if f['severity'] == 'WARNING']
        info_findings = [f for f in self.findings if f['severity'] == 'INFO']
        
        if critical_findings:
            self.diagnostics_output.append(f"\nCRITICAL ISSUES ({len(critical_findings)}):")
            for f in critical_findings:
                self.diagnostics_output.append(f"  - {f['category']}: {f['message']}")
                
        if warning_findings:
            self.diagnostics_output.append(f"\nWARNINGS ({len(warning_findings)}):")
            for f in warning_findings:
                self.diagnostics_output.append(f"  - {f['category']}: {f['message']}")
                
        self.diagnostics_output.append("\n### Likely Causes ###")
        
        causes = []
        if not any(f['category'] == 'PROCESS_STATUS' and f['severity'] == 'CRITICAL' for f in self.findings):
            pass
        else:
            causes.append("Process is not running (PID file exists but process dead)")
            
        if any(f['category'] == 'PROCESS_STATUS' and 'ZOMBIE' in f['message'] for f in self.findings):
            causes.append("Process is in zombie state - parent may have crashed")
            
        if any(f['category'] == 'LOG_EXCEPTIONS' and 'CRITICAL' in f['severity'] for f in self.findings):
            causes.append("Recent exceptions detected in logs causing stall")
            
        if any(f['category'] == 'DEPENDENCY_IMPORTS' and f['severity'] == 'CRITICAL' for f in self.findings):
            causes.append("Missing Python dependencies preventing operation")
            
        if any(f['category'] == 'SERVICE_HEALTH' and 'No entry found' in f['message'] for f in self.findings):
            causes.append("Service health entry missing - agent may never have started")
            
        if not causes:
            causes.append("No obvious failure detected - may be slow processing or network delay")
            
        for cause in causes:
            self.diagnostics_output.append(f"  - {cause}")
            
        self.diagnostics_output.append("\n### Next Steps (Diagnostics Only) ###")
        self.diagnostics_output.append("  1. Review CRITICAL findings above")
        self.diagnostics_output.append("  2. Check process/PID file consistency")
        self.diagnostics_output.append("  3. Examine specific exceptions in logs")
        self.diagnostics_output.append("  4. Verify all dependencies are installed")
        self.diagnostics_output.append("  5. Check write_service connectivity")
        
        report = "\n".join(self.diagnostics_output)
        
        report_data = {
            'report_type': 'manager_agent_stale_diagnostics',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'current_staleness': f"{CURRENT_STALENESS_SECONDS}s (threshold: {STALE_THRESHOLD_SECONDS}s)",
            'findings': self.findings,
            'causes_identified': causes,
            'report_text': report
        }
        
        return json.dumps(report_data, indent=2)


def run():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('/home/workspace/zo_sentinel/logs/diagnostics.log')
        ]
    )
    
    logger = logging.getLogger("zo_sentinel.diagnostics")
    logger.info("Starting manager_agent stale diagnostics")
    
    diagnostics = ManagerAgentStaleDiagnostics()
    
    diagnostics.check_service_health_table()
    diagnostics.check_process_status()
    diagnostics.check_log_exceptions()
    diagnostics.check_dependency_imports()
    
    report = diagnostics.compile_diagnostic_report()
    print(report)
    
    logger.info("Diagnostics complete")
    
    return 0 if len([f for f in diagnostics.findings if f['severity'] == 'CRITICAL']) == 0 else 1


if __name__ == '__main__':
    sys.exit(run())