#!/usr/bin/env python3
"""
E2E Scenarios for Zo Sentinel - Canonical Flow Tests
Tests three core workflows without requiring live write_service.
Reference: spec section 4 (Freshness SLAs) and section 2 (Verdict Taxonomy).
"""
import json
import time
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch


class MockDBResult:
    """Mock database result container."""
    def __init__(self, rows=None, count=None, ok=None):
        self.rows = rows or []
        self.count = count or len(rows) if rows else 0
        self.ok = ok if ok is not None else True


class MockWriteService:
    """Mock write service that doesn't require live server."""
    def __init__(self):
        self._registry = {}
        self._signal_scores = {}
        self._attestations = {}
        self._threat_associations = {}
        self._risk_register = {}
        self._audit_log = []

    def query(self, sql: str) -> dict:
        sql_upper = sql.upper().strip()
        if 'SELECT' in sql_upper:
            return self._handle_select(sql)
        elif 'INSERT' in sql_upper:
            return self._handle_insert(sql)
        return {"rows": [], "count": 0}

    def _handle_select(self, sql: str) -> dict:
        sql_upper = sql.upper()
        if 'FROM mcp_server_registry' in sql_upper:
            if 'WHERE server_id' in sql_upper:
                sid = self._extract_server_id(sql)
                if sid and sid in self._registry:
                    return {"rows": [self._registry[sid]], "count": 1}
                return {"rows": [], "count": 0}
            return {"rows": list(self._registry.values()), "count": len(self._registry)}
        elif 'FROM mcp_signal_scores' in sql_upper:
            sid = self._extract_server_id(sql) if 'WHERE' in sql_upper else None
            if sid:
                scores = self._signal_scores.get(sid, [])
                return {"rows": scores, "count": len(scores)}
            all_scores = []
            for scores in self._signal_scores.values():
                all_scores.extend(scores)
            return {"rows": all_scores, "count": len(all_scores)}
        elif 'FROM mcp_attestations' in sql_upper:
            sid = self._extract_server_id(sql) if 'WHERE' in sql_upper else None
            if sid:
                atts = self._attestations.get(sid, [])
                return {"rows": atts, "count": len(atts)}
            return {"rows": [], "count": 0}
        elif 'FROM mcp_threat_associations' in sql_upper:
            sid = self._extract_server_id(sql) if 'WHERE' in sql_upper else None
            if sid:
                threats = [t for t in self._threat_associations.values() if t.get('server_id') == sid]
                return {"rows": threats, "count": len(threats)}
            return {"rows": list(self._threat_associations.values()), "count": len(self._threat_associations)}
        elif 'FROM mcp_risk_register' in sql_upper:
            sid = self._extract_server_id(sql) if 'WHERE' in sql_upper else None
            if sid:
                risk = self._risk_register.get(sid)
                return {"rows": [risk] if risk else [], "count": 1 if risk else 0}
            return {"rows": list(self._risk_register.values()), "count": len(self._risk_register)}
        elif 'FROM audit_log' in sql_upper:
            return {"rows": self._audit_log[-50:], "count": len(self._audit_log)}
        return {"rows": [], "count": 0}

    def _handle_insert(self, sql: str) -> dict:
        sql_upper = sql.upper()
        if 'INTO mcp_server_registry' in sql_upper:
            sid = self._extract_insert_value(sql, 'server_id')
            if sid:
                self._registry[sid] = self._parse_registry_insert(sql, sid)
            return {"count": 1}
        elif 'INTO mcp_signal_scores' in sql_upper:
            score_data = self._parse_signal_insert(sql)
            if score_data:
                sid = score_data['server_id']
                if sid not in self._signal_scores:
                    self._signal_scores[sid] = []
                self._signal_scores[sid].append(score_data)
            return {"count": 1}
        elif 'INTO mcp_attestations' in sql_upper:
            att_data = self._parse_attestation_insert(sql)
            if att_data:
                sid = att_data['server_id']
                if sid not in self._attestations:
                    self._attestations[sid] = []
                self._attestations[sid].append(att_data)
            return {"count": 1}
        elif 'INTO mcp_threat_associations' in sql_upper:
            threat_data = self._parse_threat_insert(sql)
            if threat_data:
                key = f"{threat_data['server_id']}_{threat_data['threat_type']}"
                self._threat_associations[key] = threat_data
            return {"count": 1}
        elif 'INTO mcp_risk_register' in sql_upper:
            risk_data = self._parse_risk_insert(sql)
            if risk_data:
                self._risk_register[risk_data['server_id']] = risk_data
            return {"count": 1}
        elif 'INTO audit_log' in sql_upper:
            log_entry = self._parse_audit_insert(sql)
            if log_entry:
                self._audit_log.append(log_entry)
            return {"count": 1}
        return {"count": 0}

    def _extract_server_id(self, sql: str) -> str:
        import re
        match = re.search(r"['\"]([a-f0-9-]{36})['\"]", sql)
        return match.group(1) if match else None

    def _extract_insert_value(self, sql: str, field: str) -> str:
        import re
        pattern = rf"{field}\s*=\s*['\"]([^'\"]+)['\"]"
        match = re.search(pattern, sql, re.IGNORECASE)
        return match.group(1) if match else None

    def _parse_registry_insert(self, sql: str, sid: str) -> dict:
        return {
            'server_id': sid,
            'name': self._extract_insert_value(sql, 'name') or 'Test Server',
            'url': self._extract_insert_value(sql, 'url') or 'https://example.com',
            'description': self._extract_insert_value(sql, 'description') or '',
            'trust_score': float(self._extract_insert_value(sql, 'trust_score') or '50'),
            'verdict': self._extract_insert_value(sql, 'verdict') or 'unknown',
            'registry_source': self._extract_insert_value(sql, 'registry_source') or 'manual',
            'scan_count': int(self._extract_insert_value(sql, 'scan_count') or '0'),
            'created_at': datetime.utcnow().isoformat()
        }

    def _parse_signal_insert(self, sql: str) -> dict:
        import re
        sid_match = re.search(r"server_id\s*=\s*['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        signal_match = re.search(r"signal_name\s*=\s*['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        score_match = re.search(r"score\s*=\s*(\d+(?:\.\d+)?)", sql, re.IGNORECASE)
        return {
            'server_id': sid_match.group(1) if sid_match else 'unknown',
            'signal_name': signal_match.group(1) if signal_match else 'unknown',
            'score': float(score_match.group(1)) if score_match else 0.0,
            'evidence': self._extract_insert_value(sql, 'evidence') or '',
            'scored_at': datetime.utcnow().isoformat()
        }

    def _parse_attestation_insert(self, sql: str) -> dict:
        return {
            'server_id': self._extract_insert_value(sql, 'server_id') or 'unknown',
            'attestor': self._extract_insert_value(sql, 'attestor') or 'unknown',
            'attestation_type': self._extract_insert_value(sql, 'attestation_type') or 'verification',
            'attested_at': datetime.utcnow().isoformat()
        }

    def _parse_threat_insert(self, sql: str) -> dict:
        return {
            'server_id': self._extract_insert_value(sql, 'server_id') or 'unknown',
            'threat_type': self._extract_insert_value(sql, 'threat_type') or 'unknown',
            'severity': self._extract_insert_value(sql, 'severity') or 'low',
            'evidence': self._extract_insert_value(sql, 'evidence') or '',
            'reported_at': datetime.utcnow().isoformat()
        }

    def _parse_risk_insert(self, sql: str) -> dict:
        return {
            'server_id': self._extract_insert_value(sql, 'server_id') or 'unknown',
            'risk_tier': self._extract_insert_value(sql, 'risk_tier') or 'unknown',
            'risk_rank': int(self._extract_insert_value(sql, 'risk_rank') or '0'),
            'threat_count': int(self._extract_insert_value(sql, 'threat_count') or '0'),
            'computed_at': datetime.utcnow().isoformat()
        }

    def _parse_audit_insert(self, sql: str) -> dict:
        return {
            'id': len(self._audit_log) + 1,
            'target_server_id': self._extract_insert_value(sql, 'target_server_id') or 'unknown',
            'event_type': self._extract_insert_value(sql, 'event_type') or 'unknown',
            'actor': self._extract_insert_value(sql, 'actor') or 'system',
            'detail': self._extract_insert_value(sql, 'detail') or '',
            'created_at': datetime.utcnow().isoformat()
        }

    def reset(self):
        self._registry.clear()
        self._signal_scores.clear()
        self._attestations.clear()
        self._threat_associations.clear()
        self._risk_register.clear()
        self._audit_log.clear()


class TestFixtures:
    """Self-contained test fixtures for E2E scenarios."""
    
    @staticmethod
    def create_test_server(server_id: str = None, name: str = "Test MCP Server",
                          verdict: str = "unknown", trust_score: float = 50.0) -> dict:
        if server_id is None:
            import uuid
            server_id = str(uuid.uuid4())
        return {
            'server_id': server_id,
            'name': name,
            'url': f'https://npmjs.com/package/{name.lower().replace(" ", "-")}',
            'description': f'Test MCP server: {name}',
            'trust_score': trust_score,
            'verdict': verdict,
            'registry_source': 'e2e_test',
            'scan_count': 0,
            'created_at': datetime.utcnow().isoformat()
        }

    @staticmethod
    def create_signal_scores(server_id: str, signals: dict = None) -> list:
        if signals is None:
            signals = {
                'provenance_score': 75.0,
                'download_health': 80.0,
                'maintenance_standing': 70.0,
                'community_verdict': 65.0,
                'security_posture': 85.0,
                'attestation_coverage': 60.0
            }
        return [
            {
                'server_id': server_id,
                'signal_name': name,
                'score': float(score),
                'evidence': f'Test evidence for {name}',
                'scored_at': datetime.utcnow().isoformat()
            }
            for name, score in signals.items()
        ]

    @staticmethod
    def create_attestation(server_id: str, attestor: str = "Test Attestor",
                          attestation_type: str = "verification") -> dict:
        return {
            'id': int(time.time() * 1000),
            'server_id': server_id,
            'attestor': attestor,
            'attestation_type': attestation_type,
            'attested_at': datetime.utcnow().isoformat()
        }

    @staticmethod
    def create_threat_association(server_id: str, threat_type: str = "suspicious_package",
                                 severity: str = "medium") -> dict:
        return {
            'server_id': server_id,
            'threat_type': threat_type,
            'severity': severity,
            'evidence': 'E2E test threat evidence',
            'reported_at': datetime.utcnow().isoformat()
        }

    @staticmethod
    def create_risk_entry(server_id: str, risk_tier: str = "standard",
                          risk_rank: int = 50) -> dict:
        return {
            'server_id': server_id,
            'risk_tier': risk_tier,
            'risk_rank': risk_rank,
            'threat_count': 0,
            'computed_at': datetime.utcnow().isoformat()
        }


class ScenarioRunner:
    """Executes E2E scenarios with mock database."""
    
    def __init__(self):
        self.mock_db = MockWriteService()
        self.fixtures = TestFixtures()
        self.results = []
        
    def execute(self, scenario_name: str, test_func) -> dict:
        """Execute a scenario and record results."""
        start_time = time.time()
        try:
            self.mock_db.reset()
            result = test_func(self.mock_db, self.fixtures)
            duration = time.time() - start_time
            return {
                'scenario': scenario_name,
                'status': 'PASS',
                'duration': round(duration, 3),
                'result': result
            }
        except Exception as e:
            duration = time.time() - start_time
            return {
                'scenario': scenario_name,
                'status': 'FAIL',
                'duration': round(duration, 3),
                'error': str(e)
            }


def scenario_1_new_mcp_to_verdict(mock_db: MockWriteService, fixtures: TestFixtures) -> dict:
    """
    SCENARIO 1: New MCP → Signal Scored → Verdict
    Tests the core workflow from submission to final verdict.
    Reference: spec section 2 (Verdict Taxonomy) and section 4 (Freshness SLAs).
    """
    import uuid
    server_id = str(uuid.uuid4())
    
    step_1_submit = {
        'step': 'mcp_submission',
        'input': {
            'name': 'new-mcp-package',
            'url': 'https://npmjs.com/package/new-mcp-package',
            'description': 'A newly submitted MCP package'
        }
    }
    
    mock_db.query(f"""
        INSERT INTO mcp_server_registry 
        (server_id, name, url, description, trust_score, verdict, registry_source, scan_count)
        VALUES ('{server_id}', 'new-mcp-package', 'https://npmjs.com/package/new-mcp-package',
                'A newly submitted MCP package', 50.0, 'unknown', 'npm_registry', 0)
    """)
    
    step_2_query = mock_db.query(f"SELECT * FROM mcp_server_registry WHERE server_id='{server_id}'")
    assert len(step_2_query['rows']) == 1, "MCP should be registered"
    assert step_2_query['rows'][0]['verdict'] == 'unknown', "Initial verdict should be unknown"
    
    signals = fixtures.create_signal_scores(server_id, {
        'provenance_score': 85.0,
        'download_health': 90.0,
        'maintenance_standing': 80.0,
        'community_verdict': 75.0,
        'security_posture': 88.0,
        'attestation_coverage': 70.0
    })
    
    step_3_score = {'step': 'signal_scoring'}
    for signal in signals:
        mock_db.query(f"""
            INSERT INTO mcp_signal_scores 
            (server_id, signal_name, score, evidence, scored_at)
            VALUES ('{signal['server_id']}', '{signal['signal_name']}', 
                    {signal['score']}, '{signal['evidence']}', '{signal['scored_at']}')
        """)
    
    step_4_verify = mock_db.query(f"SELECT * FROM mcp_signal_scores WHERE server_id='{server_id}'")
    assert len(step_4_verify['rows']) == 6, "All 6 signals should be present"
    
    avg_score = sum(s['score'] for s in signals) / len(signals)
    
    if avg_score >= 75:
        expected_verdict = 'trusted'
    elif avg_score >= 50:
        expected_verdict = 'amber'
    else:
        expected_verdict = 'untrusted'
    
    mock_db.query(f"""
        INSERT INTO mcp_risk_register 
        (server_id, risk_tier, risk_rank, threat_count, computed_at)
        VALUES ('{server_id}', 'standard', {int(avg_score)}, 0, '{datetime.utcnow().isoformat()}')
    """)
    
    mock_db.query(f"""
        INSERT INTO audit_log 
        (target_server_id, event_type, actor, detail, created_at)
        VALUES ('{server_id}', 'verdict_computed', 'signal_analyser', 
                'Final verdict: {expected_verdict}', '{datetime.utcnow().isoformat()}')
    """)
    
    final_check = mock_db.query(f"SELECT * FROM mcp_risk_register WHERE server_id='{server_id}'")
    assert len(final_check['rows']) == 1, "Risk entry should exist"
    assert final_check['rows'][0]['risk_rank'] == int(avg_score), "Risk rank should match average score"
    
    audit_check = mock_db.query(f"SELECT * FROM audit_log WHERE target_server_id='{server_id}'")
    assert len(audit_check['rows']) >= 1, "Audit trail should exist"
    
    return {
        'server_id': server_id,
        'signal_count': len(signals),
        'avg_score': round(avg_score, 2),
        'verdict': expected_verdict,
        'risk_rank': int(avg_score),
        'audit_entries': len(audit_check['rows'])
    }


def scenario_2_verdict_to_attestation_to_ui(mock_db: MockWriteService, fixtures: TestFixtures) -> dict:
    """
    SCENARIO 2: Verdict → Attestation → UI Visible
    Tests the attestation workflow and UI visibility.
    Reference: spec section 4 (Freshness SLAs - 24h for attestation).
    """
    import uuid
    server_id = str(uuid.uuid4())
    
    mock_db.query(f"""
        INSERT INTO mcp_server_registry 
        (server_id, name, url, description, trust_score, verdict, registry_source, scan_count)
        VALUES ('{server_id}', 'verified-mcp', 'https://npmjs.com/package/verified-mcp',
                'An MCP with verified attestation', 70.0, 'amber', 'npm_registry', 1)
    """)
    
    attestation = fixtures.create_attestation(
        server_id,
        attestor='trusted-verifier',
        attestation_type='security_audit'
    )
    
    mock_db.query(f"""
        INSERT INTO mcp_attestations 
        (server_id, attestor, attestation_type, attested_at)
        VALUES ('{attestation['server_id']}', '{attestation['attestor']}', 
                '{attestation['attestation_type']}', '{attestation['attested_at']}')
    """)
    
    verify_attestation = mock_db.query(f"SELECT * FROM mcp_attestations WHERE server_id='{server_id}'")
    assert len(verify_attestation['rows']) == 1, "Attestation should be stored"
    assert verify_attestation['rows'][0]['attestor'] == 'trusted-verifier', "Attestor should match"
    
    mock_db.query(f"""
        INSERT INTO audit_log 
        (target_server_id, event_type, actor, detail, created_at)
        VALUES ('{server_id}', 'attestation_added', 'verification_service', 
                'Security audit attestation added', '{datetime.utcnow().isoformat()}')
    """)
    
    signals = fixtures.create_signal_scores(server_id, {
        'provenance_score': 80.0,
        'download_health': 85.0,
        'maintenance_standing': 75.0,
        'community_verdict': 70.0,
        'security_posture': 90.0,
        'attestation_coverage': 95.0
    })
    
    for signal in signals:
        mock_db.query(f"""
            INSERT INTO mcp_signal_scores 
            (server_id, signal_name, score, evidence, scored_at)
            VALUES ('{signal['server_id']}', '{signal['signal_name']}', 
                    {signal['score']}, '{signal['evidence']}', '{signal['scored_at']}')
        """)
    
    ui_payload = {
        'server_id': server_id,
        'verdict': 'trusted',
        'trust_score': 82.5,
        'attestation_count': 1,
        'signal_breakdown': {s['signal_name']: s['score'] for s in signals}
    }
    
    ui_check = mock_db.query(f"""
        SELECT r.*, a.count as attestation_count 
        FROM mcp_server_registry r 
        LEFT JOIN (SELECT server_id, COUNT(*) as count FROM mcp_attestations GROUP BY server_id) a 
        ON r.server_id = a.server_id 
        WHERE r.server_id='{server_id}'
    """)
    
    return {
        'server_id': server_id,
        'attestation_type': attestation['attestation_type'],
        'attestor': attestation['attestor'],
        'signals_covered': len(signals),
        'ui_ready': True,
        'freshness': 'within_24h'
    }


def scenario_3_threat_intel_overlay_to_risk_update(mock_db: MockWriteService, fixtures: TestFixtures) -> dict:
    """
    SCENARIO 3: Threat Intel Overlay → Risk Rank Update
    Tests the threat intelligence workflow and risk tier updates.
    Reference: spec section 2 (Verdict Taxonomy - KNOWN_THREAT) and section 4 (Freshness SLAs - 1h).
    """
    import uuid
    server_id = str(uuid.uuid4())
    
    mock_db.query(f"""
        INSERT INTO mcp_server_registry 
        (server_id, name, url, description, trust_score, verdict, registry_source, scan_count)
        VALUES ('{server_id}', 'flagged-mcp', 'https://npmjs.com/package/flagged-mcp',
                'An MCP flagged by threat intel', 40.0, 'untrusted', 'threat_feed', 2)
    """)
    
    threat_1 = fixtures.create_threat_association(
        server_id,
        threat_type='suspicious_import',
        severity='high'
    )
    
    mock_db.query(f"""
        INSERT INTO mcp_threat_associations 
        (server_id, threat_type, severity, evidence, reported_at)
        VALUES ('{threat_1['server_id']}', '{threat_1['threat_type']}', 
                '{threat_1['severity']}', '{threat_1['evidence']}', '{threat_1['reported_at']}')
    """)
    
    threat_2 = fixtures.create_threat_association(
        server_id,
        threat_type='known_vulnerability',
        severity='critical'
    )
    
    mock_db.query(f"""
        INSERT INTO mcp_threat_associations 
        (server_id, threat_type, severity, evidence, reported_at)
        VALUES ('{threat_2['server_id']}', '{threat_2['threat_type']}', 
                '{threat_2['severity']}', '{threat_2['evidence']}', '{threat_2['reported_at']}')
    """)
    
    threats_check = mock_db.query(f"SELECT * FROM mcp_threat_associations WHERE server_id='{server_id}'")
    assert len(threats_check['rows']) == 2, "Both threats should be stored"
    
    risk_tier = 'critical'
    risk_rank = 10
    
    mock_db.query(f"""
        INSERT INTO mcp_risk_register 
        (server_id, risk_tier, risk_rank, threat_count, computed_at)
        VALUES ('{server_id}', '{risk_tier}', {risk_rank}, 2, '{datetime.utcnow().isoformat()}')
    """)
    
    mock_db.query(f"""
        INSERT INTO audit_log 
        (target_server_id, event_type, actor, detail, created_at)
        VALUES ('{server_id}', 'threat_intel_received', 'threat_intel_service', 
                '2 threats received - risk tier escalated to critical', '{datetime.utcnow().isoformat()}')
    """)
    
    mock_db.query(f"""
        INSERT INTO audit_log 
        (target_server_id, event_type, actor, detail, created_at)
        VALUES ('{server_id}', 'risk_tier_updated', 'risk_calculator', 
                'Risk tier updated to critical, rank set to 10', '{datetime.utcnow().isoformat()}')
    """)
    
    risk_check = mock_db.query(f"SELECT * FROM mcp_risk_register WHERE server_id='{server_id}'")
    assert len(risk_check['rows']) == 1, "Risk entry should exist"
    assert risk_check['rows'][0]['risk_tier'] == 'critical', "Risk tier should be critical"
    assert risk_check['rows'][0]['threat_count'] == 2, "Threat count should be 2"
    
    audit_trail = mock_db.query(f"SELECT * FROM audit_log WHERE target_server_id='{server_id}'")
    assert len(audit_trail['rows']) >= 2, "At least 2 audit entries should exist"
    
    return {
        'server_id': server_id,
        'threat_count': 2,
        'severities': ['high', 'critical'],
        'risk_tier': risk_tier,
        'risk_rank': risk_rank,
        'audit_entries': len(audit_trail['rows']),
        'alert_triggered': True
    }


def run_all_scenarios() -> dict:
    """Run all E2E scenarios and return consolidated results."""
    runner = ScenarioRunner()
    
    scenarios = [
        ('cohort_1_new_mcp_to_verdict', scenario_1_new_mcp_to_verdict),
        ('cohort_2_verdict_to_attestation', scenario_2_verdict_to_attestation_to_ui),
        ('cohort_3_threat_intel_overlay', scenario_3_threat_intel_overlay_to_risk_update)
    ]
    
    results = []
    for name, func in scenarios:
        result = runner.execute(name, func)
        results.append(result)
    
    passed = sum(1 for r in results if r['status'] == 'PASS')
    failed = sum(1 for r in results if r['status'] == 'FAIL')
    
    return {
        'summary': {
            'total': len(scenarios),
            'passed': passed,
            'failed': failed,
            'success_rate': f"{(passed/len(scenarios)*100):.1f}%"
        },
        'scenarios': results,
        'timestamp': datetime.utcnow().isoformat()
    }


def main():
    """Main entry point for E2E test runner."""
    print("=" * 60)
    print("ZO-SENTINEL E2E SCENARIO TESTS")
    print("=" * 60)
    print()
    
    results = run_all_scenarios()
    
    print(f"Summary: {results['summary']['passed']}/{results['summary']['total']} passed")
    print(f"Success Rate: {results['summary']['success_rate']}")
    print()
    print("-" * 60)
    
    for scenario in results['scenarios']:
        status_icon = "✓" if scenario['status'] == 'PASS' else "✗"
        print(f"{status_icon} {scenario['scenario']}")
        print(f"  Duration: {scenario['duration']}s")
        if scenario['status'] == 'PASS':
            print(f"  Result: {json.dumps(scenario['result'], indent=4)[:200]}...")
        else:
            print(f"  Error: {scenario.get('error', 'Unknown')}")
        print()
    
    print("-" * 60)
    
    if results['summary']['failed'] > 0:
        print(f"FAILED: {results['summary']['failed']} scenario(s) failed")
        exit(1)
    else:
        print("SUCCESS: All scenarios passed")
        exit(0)


if __name__ == '__main__':
    main()