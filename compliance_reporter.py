#!/usr/bin/env python3
"""
compliance_reporter.py -- ZO-SENTINEL Compliance Reporter
Generates COMPLIANCE_REPORT.md and exports CSV reports.
Run: python3 compliance_reporter.py --report  (default)
     python3 compliance_reporter.py --csv
     python3 compliance_reporter.py --both
"""

import os
import sys
import csv
import argparse
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

import requests

# Configuration
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
REPORT_DIR = '/home/workspace/zo_sentinel/reports'
REPORT_PATH = f'{REPORT_DIR}/COMPLIANCE_REPORT.md'
CSV_PATH = f'{REPORT_DIR}/registry_export.csv'
LOG_DIR = '/home/workspace/zo_sentinel/logs'

# Verdict categories mapping
VERDICT_CATEGORIES = {
    'TRUSTED_GENERAL': 'APPROVED',
    'TRUSTED_RESEARCH': 'APPROVED',
    'ENTERPRISE_CONTROLLED': 'APPROVED',
    'CAUTION_LIMITED': 'PENDING_REVIEW',
    'CAUTION_ELEVATED': 'PENDING_REVIEW',
    'HIGH_RISK_ISOLATED': 'BLOCKED',
    'KNOWN_THREAT': 'BLOCKED',
    'INSUFFICIENT': 'PENDING_REVIEW'
}

# Risk tier to severity mapping
RISK_TIER_SEVERITY = {
    'CRITICAL': 'CRITICAL',
    'HIGH': 'HIGH',
    'MEDIUM': 'MEDIUM',
    'LOW': 'LOW'
}

# Logging setup
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/compliance_reporter.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('compliance_reporter')


def ws_query(sql: str, params: Optional[List] = None) -> Dict[str, Any]:
    """Execute SQL query against DuckDB via inference_router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return {'error': str(e), 'data': []}


def get_registry_summary() -> Dict[str, Any]:
    """Get summary statistics from mcp_server_registry."""
    result = {
        'total_servers': 0,
        'approved_count': 0,
        'blocked_count': 0,
        'pending_count': 0,
        'servers': []
    }
    
    sql = """
    SELECT 
        server_id,
        name,
        verdict,
        trust_score,
        risk_tier,
        last_assessed,
        status
    FROM mcp_server_registry
    ORDER BY trust_score ASC NULLS LAST
    """
    
    data = ws_query(sql)
    if 'data' in data:
        result['servers'] = data['data']
        result['total_servers'] = len(data['data'])
        
        for row in data['data']:
            verdict = row.get('verdict') or 'INSUFFICIENT'
            category = VERDICT_CATEGORIES.get(verdict, 'PENDING_REVIEW')
            
            if category == 'APPROVED':
                result['approved_count'] += 1
            elif category == 'BLOCKED':
                result['blocked_count'] += 1
            else:
                result['pending_count'] += 1
    
    return result


def get_risk_distribution() -> Dict[str, int]:
    """Get risk tier distribution from mcp_server_registry."""
    distribution = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    
    sql = """
    SELECT risk_tier, COUNT(*) as count
    FROM mcp_server_registry
    WHERE risk_tier IS NOT NULL
    GROUP BY risk_tier
    """
    
    data = ws_query(sql)
    if 'data' in data:
        for row in data['data']:
            tier = row.get('risk_tier', '').upper()
            if tier in distribution:
                distribution[tier] = row.get('count', 0)
    
    return distribution


def get_expired_attestations() -> List[Dict[str, Any]]:
    """Get servers with expired attestations."""
    expired = []
    now = datetime.now(timezone.utc).isoformat()
    
    # Try mcp_attestations table
    sql = """
    SELECT 
        server_id,
        valid_until,
        verdict,
        generated_at
    FROM mcp_attestations
    WHERE valid_until < ?
    ORDER BY valid_until ASC
    """
    
    data = ws_query(sql, [now])
    if 'data' in data:
        for row in data['data']:
            expired.append({
                'server_id': row.get('server_id'),
                'verdict': row.get('verdict'),
                'expired_on': row.get('valid_until'),
                'days_overdue': calculate_days_overdue(row.get('valid_until'))
            })
    
    return expired


def calculate_days_overdue(valid_until: str) -> int:
    """Calculate days since attestation expired."""
    if not valid_until:
        return 0
    try:
        expiry = datetime.fromisoformat(valid_until.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = now - expiry
        return max(0, delta.days)
    except:
        return 0


def get_policy_violations() -> List[Dict[str, Any]]:
    """Get recent BLOCK decisions from mcp_decisions."""
    violations = []
    
    # Try mcp_decisions table
    sql = """
    SELECT 
        server_id,
        server_name,
        decision,
        reason,
        decided_at
    FROM mcp_decisions
    WHERE decision = 'BLOCK'
    ORDER BY decided_at DESC
    LIMIT 100
    """
    
    data = ws_query(sql)
    if 'data' in data:
        for row in data['data']:
            violations.append({
                'server_id': row.get('server_id'),
                'server_name': row.get('server_name'),
                'reason': row.get('reason'),
                'decided_at': row.get('decided_at')
            })
    
    # Fallback: check registry for high-risk entries
    if not violations:
        sql_fallback = """
        SELECT 
            server_id,
            name,
            verdict,
            risk_tier
        FROM mcp_server_registry
        WHERE verdict IN ('HIGH_RISK_ISOLATED', 'KNOWN_THREAT')
        ORDER BY last_seen DESC
        LIMIT 50
        """
        data_fallback = ws_query(sql_fallback)
        if 'data' in data_fallback:
            for row in data_fallback:
                violations.append({
                    'server_id': row.get('server_id'),
                    'server_name': row.get('name'),
                    'reason': f"High-risk verdict: {row.get('verdict')}",
                    'decided_at': row.get('last_assessed') or row.get('last_seen')
                })
    
    return violations


def generate_recommendations(summary: Dict, risk_dist: Dict, expired: List, violations: List) -> List[str]:
    """Generate top 3 recommendations based on data."""
    recommendations = []
    
    total = summary.get('total_servers', 0) or 1
    
    # Recommendation 1: Address expired attestations
    if expired:
        recommendations.append(
            f"URGENT: Address {len(expired)} expired attestation(s). "
            f"Servers with expired attestations require re-review before deployment."
        )
    else:
        recommendations.append(
            "GOOD: All attestations are current. Continue regular review cycles."
        )
    
    # Recommendation 2: Policy violations / high-risk servers
    high_risk = risk_dist.get('HIGH', 0) + risk_dist.get('CRITICAL', 0)
    if high_risk > 0:
        high_pct = (high_risk / total) * 100
        recommendations.append(
            f"ALERT: {high_risk} high/critical risk servers detected ({high_pct:.1f}% of total). "
            f"Consider implementing stricter approval workflows for high-risk categories."
        )
    
    # Recommendation 3: Pending reviews
    pending = summary.get('pending_count', 0)
    if pending > total * 0.3:
        recommendations.append(
            f"ACTION: {pending} servers ({pending/total*100:.1f}%) pending review. "
            f"Expedite determination to improve security posture visibility."
        )
    elif pending > 0:
        recommendations.append(
            f"REVIEW: {pending} servers awaiting verdict determination. "
            f"Prioritize based on risk_tier to minimize exposure window."
        )
    
    # Recommendation 4: Approved ratio
    approved = summary.get('approved_count', 0)
    approved_pct = (approved / total) * 100
    if approved_pct < 30:
        recommendations.append(
            f"INFO: Only {approved} servers ({approved_pct:.1f}%) approved. "
            f"Review approval criteria to ensure they're not overly restrictive."
        )
    
    return recommendations[:3]


def generate_report() -> str:
    """Generate the compliance report markdown."""
    logger.info("Generating compliance report...")
    
    summary = get_registry_summary()
    risk_dist = get_risk_distribution()
    expired = get_expired_attestations()
    violations = get_policy_violations()
    recommendations = generate_recommendations(summary, risk_dist, expired, violations)
    
    total = summary.get('total_servers', 0) or 1
    approved = summary.get('approved_count', 0)
    blocked = summary.get('blocked_count', 0)
    pending = summary.get('pending_count', 0)
    
    now = datetime.now(timezone.utc)
    report_date = now.strftime('%Y-%m-%d %H:%M:%S UTC')
    
    report = f"""# ZO-SENTINEL Compliance Report

**Generated:** {report_date}  
**Report Type:** Enterprise MCP Server Security Assessment

---

## 1. Executive Summary

| Metric | Value |
|--------|-------|
| Total MCP Servers | {total} |
| Approved for Deployment | {approved} ({approved/total*100:.1f}%) |
| Blocked / High Risk | {blocked} ({blocked/total*100:.1f}%) |
| Pending Review | {pending} ({pending/total*100:.1f}%) |

### Verdict Distribution

| Verdict | Count | Status |
|---------|-------|--------|
"""
    
    # Add verdict breakdown
    verdict_counts = {}
    for row in summary.get('servers', []):
        v = row.get('verdict') or 'INSUFFICIENT'
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
    
    for verdict, count in sorted(verdict_counts.items(), key=lambda x: -x[1]):
        category = VERDICT_CATEGORIES.get(verdict, 'UNKNOWN')
        report += f"| {verdict} | {count} | {category} |\n"
    
    report += f"""
### Governance Status

- **Compliance Posture:** {'HEALTHY' if expired == 0 and blocked < total * 0.2 else 'REQUIRES_ATTENTION'}
- **Attestation Currency:** {'VALID' if len(expired) == 0 else f'{len(expired)} EXPIRED'}
- **Risk Exposure:** {'CONTROLLED' if blocked < total * 0.1 else 'ELEVATED'}

---

## 2. Risk Distribution

| Risk Tier | Count | Percentage |
|-----------|-------|------------|
"""
    
    for tier in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
        count = risk_dist.get(tier, 0)
        pct = (count / total) * 100 if total > 0 else 0
        report += f"| {tier} | {count} | {pct:.1f}% |\n"
    
    # Risk bar visualization
    critical_pct = (risk_dist.get('CRITICAL', 0) / total) * 50 if total > 0 else 0
    high_pct = (risk_dist.get('HIGH', 0) / total) * 50 if total > 0 else 0
    medium_pct = (risk_dist.get('MEDIUM', 0) / total) * 50 if total > 0 else 0
    low_pct = (risk_dist.get('LOW', 0) / total) * 50 if total > 0 else 0
    
    report += f"""
### Risk Distribution Visualization

```
CRITICAL [{'█' * int(critical_pct)}{'░' * (50 - int(critical_pct))}] {risk_dist.get('CRITICAL', 0)}
HIGH     [{'█' * int(high_pct)}{'░' * (50 - int(high_pct))}] {risk_dist.get('HIGH', 0)}
MEDIUM   [{'█' * int(medium_pct)}{'░' * (50 - int(medium_pct))}] {risk_dist.get('MEDIUM', 0)}
LOW      [{'█' * int(low_pct)}{'░' * (50 - int(low_pct))}] {risk_dist.get('LOW', 0)}
```

---

## 3. Expired Attestations

"""
    
    if expired:
        report += f"| Server ID | Verdict | Expired On | Days Overdue |\n"
        report += "|-----------|---------|------------|---------------|\n"
        for item in expired[:20]:  # Limit to 20 entries
            report += f"| {item['server_id']} | {item['verdict']} | {item['expired_on']} | {item['days_overdue']} |\n"
        
        if len(expired) > 20:
            report += f"\n*Showing first 20 of {len(expired)} expired attestations*\n"
        
        report += f"""
### Impact Assessment

- **Compliance Risk:** HIGH - Expired attestations indicate unverified security posture
- **Recommended Action:** Re-assess all expired servers before any deployment
- **Audit Trail:** Maintain records of re-review decisions

"""
    else:
        report += """
**Status:** ✅ All attestations are current

No expired attestations detected. All registered MCP servers have valid security attestations.

"""

    report += f"""---

## 4. Policy Violations

"""
    
    if violations:
        report += f"| Server | Reason | Decision Date |\n"
        report += "|--------|--------|----------------|\n"
        for v in violations[:30]:  # Limit to 30 entries
            reason = (v.get('reason') or 'N/A')[:60]
            report += f"| {v.get('server_id', 'N/A')} | {reason} | {v.get('decided_at', 'N/A')} |\n"
        
        if len(violations) > 30:
            report += f"\n*Showing first 30 of {len(violations)} policy violations*\n"
        
        report += f"""
### Violation Summary

- **Total Blocked Servers:** {len(violations)}
- **Policy Enforcement:** Active
- **Override Capability:** Requires elevated privileges

"""
    else:
        report += """
**Status:** ✅ No policy violations detected

No BLOCK decisions found in the assessment history. All processed servers meet current policy requirements.

"""

    report += """---

## 5. Recommendations

"""
    
    for i, rec in enumerate(recommendations, 1):
        report += f"{i}. {rec}\n"
    
    report += f"""

---

## 6. Compliance Metrics

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Attestation Currency | 100% | {(1 - len(expired)/max(total,1))*100:.1f}% | {'✅' if len(expired) == 0 else '⚠️'} |
| Policy Violations | 0 | {len(violations)} | {'✅' if len(violations) == 0 else '⚠️'} |
| Risk Distribution | <10% Critical | {risk_dist.get('CRITICAL', 0)/max(total,1)*100:.1f}% | {'✅' if risk_dist.get('CRITICAL', 0) == 0 else '⚠️'} |
| Pending Reviews | <20% | {pending/total*100:.1f}% | {'✅' if pending < total * 0.2 else '⚠️'} |

---

## 7. Next Review Cycle

- **Next Scheduled Review:** {generate_next_review_date(now)}
- **Auto-escalation Trigger:** Critical findings or >5% expired attestations
- **Reporting Frequency:** Weekly

---

*Report generated by ZO-SENTINEL Compliance Reporter*
*For questions, contact the InfoSec team.*
"""
    
    return report


def generate_next_review_date(current: datetime) -> str:
    """Calculate next review date (7 days from now)."""
    next_date = current + timedelta(days=7)
    return next_date.strftime('%Y-%m-%d')


def generate_csv() -> str:
    """Export registry data to CSV."""
    logger.info("Generating CSV export...")
    
    sql = """
    SELECT 
        server_id,
        name,
        registry_source,
        url,
        description,
        verdict,
        verdict_reasoning,
        trust_score,
        confidence,
        risk_tier,
        last_assessed,
        first_seen,
        last_seen,
        scan_count,
        status
    FROM mcp_server_registry
    ORDER BY trust_score ASC NULLS LAST
    """
    
    data = ws_query(sql)
    rows = data.get('data', [])
    
    # CSV headers
    headers = [
        'server_id', 'name', 'registry_source', 'url', 'description',
        'verdict', 'verdict_reasoning', 'trust_score', 'confidence',
        'risk_tier', 'last_assessed', 'first_seen', 'last_seen',
        'scan_count', 'status', 'compliance_category'
    ]
    
    csv_content = ','.join(headers) + '\n'
    
    for row in rows:
        values = []
        for h in headers[:-1]:  # All except computed field
            val = str(row.get(h) or '').replace(',', ';').replace('\n', ' ')
            values.append(val)
        
        # Compute compliance category
        verdict = row.get('verdict') or 'INSUFFICIENT'
        category = VERDICT_CATEGORIES.get(verdict, 'UNKNOWN')
        values.append(category)
        
        csv_content += ','.join(f'"{v}"' if ',' in v else v for v in values) + '\n'
    
    return csv_content


def save_report(content: str, path: str) -> bool:
    """Save report content to file."""
    try:
        with open(path, 'w') as f:
            f.write(content)
        logger.info(f"Report saved to {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save report: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='ZO-SENTINEL Compliance Reporter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 compliance_reporter.py --report    # Generate markdown report
  python3 compliance_reporter.py --csv       # Export CSV data
  python3 compliance_reporter.py --both      # Generate both report and CSV
        """
    )
    
    parser.add_argument('--report', action='store_true',
                        help='Generate COMPLIANCE_REPORT.md')
    parser.add_argument('--csv', action='store_true',
                        help='Export registry to CSV')
    parser.add_argument('--both', action='store_true',
                        help='Generate both report and CSV')
    parser.add_argument('--output-dir', default=REPORT_DIR,
                        help=f'Output directory (default: {REPORT_DIR})')
    
    args = parser.parse_args()
    
    # Default to --report if no args
    if not any([args.report, args.csv, args.both]):
        args.report = True
    
    if args.output_dir != REPORT_DIR:
        global REPORT_PATH, CSV_PATH
        REPORT_PATH = os.path.join(args.output_dir, 'COMPLIANCE_REPORT.md')
        CSV_PATH = os.path.join(args.output_dir, 'registry_export.csv')
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    success_count = 0
    
    if args.report or args.both:
        report = generate_report()
        if save_report(report, REPORT_PATH):
            success_count += 1
            print(f"✓ Report generated: {REPORT_PATH}")
        else:
            print(f"✗ Failed to generate report")
    
    if args.csv or args.both:
        csv_content = generate_csv()
        if save_report(csv_content, CSV_PATH):
            success_count += 1
            print(f"✓ CSV exported: {CSV_PATH}")
        else:
            print(f"✗ Failed to export CSV")
    
    if success_count == 0:
        logger.error("No outputs generated")
        sys.exit(1)
    
    logger.info(f"Compliance reporting complete. {success_count} output(s) generated.")


if __name__ == '__main__':
    main()