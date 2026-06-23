# diagnose_tool_count_signal_discrimination_v3.py
"""
Diagnostic Tool: Tool Count Signal Discrimination Analysis v3

Investigates why tool_count signal has only 2 distinct values across all MCPs.
Queries mcp_signal_scores table and analyzes score distribution.

Read prior diagnostic files for context on this investigation.
"""

import os
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta

# Database imports
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Try importing project modules
try:
    from config.database import get_database_config
    from utils.logging_utils import get_logger
except ImportError:
    # Fallback definitions
    def get_logger(name):
        import logging
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger(name)
    
    def get_database_config():
        return {"type": "sqlite", "db_path": "zo_sentinel.db"}

logger = get_logger(__name__)


def read_prior_diagnostics():
    """Read prior diagnostic files to understand investigation history."""
    prior_findings = {}
    diagnostic_files = [
        PROJECT_ROOT / "investigate_tool_count_improvement.py",
        PROJECT_ROOT / "diagnose_tool_count_weak_signal.py",
        PROJECT_ROOT / "diagnose_tool_count_signal_discrimination.py",
        PROJECT_ROOT / "diagnose_tool_count_signal_discrimination_v2.py",
    ]
    
    for filepath in diagnostic_files:
        if filepath.exists():
            logger.info(f"Reading prior diagnostic: {filepath.name}")
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                    prior_findings[filepath.name] = {
                        'path': str(filepath),
                        'content': content[:5000]  # First 5000 chars
                    }
                    logger.info(f"  - Found {len(content)} bytes")
            except Exception as e:
                logger.warning(f"  - Could not read: {e}")
    
    return prior_findings


def connect_to_database():
    """Establish database connection."""
    config = get_database_config()
    
    if config.get('type') == 'postgresql':
        conn = psycopg2.connect(
            host=config.get('host', 'localhost'),
            port=config.get('port', 5432),
            database=config.get('database'),
            user=config.get('user'),
            password=config.get('password')
        )
    else:
        db_path = config.get('db_path', 'zo_sentinel.db')
        if not os.path.isabs(db_path):
            db_path = PROJECT_ROOT / db_path
        conn = sqlite3.connect(db_path)
    
    return conn


def query_tool_count_scores(conn, db_type='sqlite'):
    """Query all tool_count signal scores from database."""
    query = """
        SELECT 
            mss.id,
            mss.mcp_id,
            mss.signal_type,
            mss.score,
            mss.bucket,
            m.name as mcp_name,
            m.tool_count,
            m.capabilities
        FROM mcp_signal_scores mss
        JOIN mcps m ON m.id = mss.mcp_id
        WHERE mss.signal_type = 'tool_count'
        ORDER BY mss.score, mss.mcp_id
    """
    
    cursor = conn.cursor() if db_type == 'sqlite' else conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query)
    rows = cursor.fetchall()
    
    results = []
    for row in rows:
        if db_type == 'sqlite':
            results.append({
                'id': row[0],
                'mcp_id': row[1],
                'signal_type': row[2],
                'score': row[3],
                'bucket': row[4],
                'mcp_name': row[5],
                'tool_count': row[6],
                'capabilities': row[7]
            })
        else:
            results.append(dict(row))
    
    return results


def query_raw_tool_counts(conn, db_type='sqlite'):
    """Query raw tool counts from MCPs table."""
    query = """
        SELECT 
            id,
            name,
            tool_count,
            capabilities
        FROM mcps
        WHERE tool_count IS NOT NULL
        ORDER BY tool_count
    """
    
    cursor = conn.cursor() if db_type == 'sqlite' else conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query)
    rows = cursor.fetchall()
    
    results = []
    for row in rows:
        if db_type == 'sqlite':
            results.append({
                'id': row[0],
                'name': row[1],
                'tool_count': row[2],
                'capabilities': row[3]
            })
        else:
            results.append(dict(row))
    
    return results


def analyze_score_distribution(scores):
    """Analyze the distribution of tool_count scores."""
    if not scores:
        return {"error": "No scores found"}
    
    score_values = [s['score'] for s in scores]
    distinct_scores = sorted(set(score_values))
    score_counts = Counter(score_values)
    
    analysis = {
        'total_records': len(scores),
        'distinct_score_values': len(distinct_scores),
        'distinct_scores': distinct_scores,
        'score_distribution': dict(score_counts),
        'score_percentages': {
            score: round((count / len(scores)) * 100, 2) 
            for score, count in score_counts.items()
        }
    }
    
    # Analyze by bucket
    bucket_scores = {}
    for s in scores:
        bucket = s.get('bucket', 'NULL')
        if bucket not in bucket_scores:
            bucket_scores[bucket] = []
        bucket_scores[bucket].append(s['score'])
    
    analysis['bucket_analysis'] = {
        bucket: {
            'count': len(scores_list),
            'unique_scores': sorted(set(scores_list)),
            'score_counts': dict(Counter(scores_list))
        }
        for bucket, scores_list in bucket_scores.items()
    }
    
    return analysis


def analyze_tool_count_distribution(mcps):
    """Analyze the distribution of actual tool counts in registry."""
    if not mcps:
        return {"error": "No MCPs found"}
    
    tool_counts = [m['tool_count'] for m in mcps if m['tool_count'] is not None]
    
    if not tool_counts:
        return {"error": "No tool counts found"}
    
    distinct_counts = sorted(set(tool_counts))
    count_distribution = Counter(tool_counts)
    
    analysis = {
        'total_mcps_with_tool_count': len(tool_counts),
        'distinct_tool_counts': len(distinct_counts),
        'distinct_values': distinct_counts,
        'tool_count_distribution': dict(sorted(count_distribution.items())),
        'min_tool_count': min(tool_counts),
        'max_tool_count': max(tool_counts),
        'range': max(tool_counts) - min(tool_counts) if tool_counts else 0
    }
    
    return analysis


def analyze_bucket_granularity(scores, tool_counts_analysis):
    """Analyze if scoring buckets are too coarse."""
    if 'error' in tool_counts_analysis:
        return {"error": tool_counts_analysis['error']}
    
    # Get bucket definitions from signal configuration
    # Assuming standard bucket definitions
    bucket_ranges = {
        'NONE': 0,
        'MINIMAL': (1, 5),
        'BASIC': (6, 15),
        'MODERATE': (16, 50),
        'EXTENSIVE': (51, 150),
        'COMPREHENSIVE': (151, 500),
        'VAST': (501, 1000),
        'MASSIVE': (1001, float('inf'))
    }
    
    # Map tool_count to expected bucket
    def get_expected_bucket(tool_count):
        if tool_count == 0:
            return 'NONE'
        elif tool_count <= 5:
            return 'MINIMAL'
        elif tool_count <= 15:
            return 'BASIC'
        elif tool_count <= 50:
            return 'MODERATE'
        elif tool_count <= 150:
            return 'EXTENSIVE'
        elif tool_count <= 500:
            return 'COMPREHENSIVE'
        elif tool_count <= 1000:
            return 'VAST'
        else:
            return 'MASSIVE'
    
    # Analyze actual vs expected buckets
    bucket_alignment = []
    for s in scores:
        actual_bucket = s.get('bucket', 'NULL')
        tool_count = s.get('tool_count')
        expected_bucket = get_expected_bucket(tool_count) if tool_count is not None else 'UNKNOWN'
        aligned = actual_bucket == expected_bucket
        bucket_alignment.append({
            'mcp_name': s.get('mcp_name'),
            'tool_count': tool_count,
            'actual_bucket': actual_bucket,
            'expected_bucket': expected_bucket,
            'aligned': aligned
        })
    
    aligned_count = sum(1 for a in bucket_alignment if a['aligned'])
    
    return {
        'bucket_definitions': bucket_ranges,
        'bucket_mapping_function': 'tool_count -> bucket based on ranges',
        'total_evaluated': len(bucket_alignment),
        'aligned_count': aligned_count,
        'alignment_rate': round((aligned_count / len(bucket_alignment)) * 100, 2) if bucket_alignment else 0,
        'misalignments': [a for a in bucket_alignment if not a['aligned']][:10]  # First 10
    }


def analyze_score_to_tool_count_mapping(scores):
    """Analyze how scores map to actual tool counts."""
    score_to_counts = {}
    
    for s in scores:
        score = s['score']
        tool_count = s.get('tool_count')
        if score not in score_to_counts:
            score_to_counts[score] = []
        if tool_count is not None:
            score_to_counts[score].append(tool_count)
    
    mapping_analysis = {}
    for score, counts in sorted(score_to_counts.items()):
        mapping_analysis[str(score)] = {
            'count_of_mcps': len(counts),
            'tool_count_min': min(counts) if counts else None,
            'tool_count_max': max(counts) if counts else None,
            'tool_count_avg': round(sum(counts) / len(counts), 2) if counts else None,
            'tool_counts': sorted(set(counts)),
            'num_distinct_tool_counts': len(set(counts))
        }
    
    return mapping_analysis


def identify_discrimination_issues(scores, tool_counts_analysis, score_mapping):
    """Identify specific issues with signal discrimination."""
    issues = []
    
    # Issue 1: Only 2 distinct score values
    distinct_scores = len(set(s['score'] for s in scores))
    if distinct_scores == 2:
        issues.append({
            'severity': 'CRITICAL',
            'type': 'LOW_DISCRIMINATION',
            'description': f'Only {distinct_scores} distinct score values found',
            'values': sorted(set(s['score'] for s in scores)),
            'recommendation': 'Investigate why score calculation produces only 2 values'
        })
    
    # Issue 2: Tool count distribution analysis
    if 'error' not in tool_counts_analysis:
        if tool_counts_analysis['distinct_tool_counts'] < 10:
            issues.append({
                'severity': 'HIGH',
                'type': 'LOW_VARIATION_IN_REGISTRY',
                'description': f'Only {tool_counts_analysis["distinct_tool_counts"]} distinct tool counts in registry',
                'values': tool_counts_analysis['distinct_values'],
                'recommendation': 'Registry may not have enough variation to differentiate MCPs'
            })
    
    # Issue 3: Score mapping overlap
    if score_mapping:
        score_values = list(score_mapping.keys())
        if len(score_values) == 2:
            low_score_counts = score_mapping[score_values[0]]['tool_counts'] if score_values[0] in score_mapping else []
            high_score_counts = score_mapping[score_values[1]]['tool_counts'] if score_values[1] in score_mapping else []
            
            if low_score_counts and high_score_counts:
                overlap = set(low_score_counts) & set(high_score_counts)
                if overlap:
                    issues.append({
                        'severity': 'HIGH',
                        'type': 'BUCKET_OVERLAP',
                        'description': f'Score buckets have overlapping tool counts: {overlap}',
                        'low_score_range': f"{min(low_score_counts)}-{max(low_score_counts)}",
                        'high_score_range': f"{min(high_score_counts)}-{max(high_score_counts)}",
                        'recommendation': 'Adjust bucket boundaries to eliminate overlap'
                    })
    
    # Issue 4: Score calculation edge case
    score_values = sorted(set(s['score'] for s in scores))
    if score_values:
        if score_values[0] == 0 and score_values[-1] == 1:
            issues.append({
                'severity': 'MEDIUM',
                'type': 'BINARY_SCORING',
                'description': 'Scores are binary (0/1), indicating possible boolean logic',
                'values': score_values,
                'recommendation': 'Replace boolean check with normalized score based on tool_count'
            })
    
    return issues


def generate_diagnostic_report(scores, tool_counts_analysis, score_distribution, 
                               bucket_analysis, score_mapping, issues, prior_findings):
    """Generate comprehensive diagnostic report."""
    
    report = []
    report.append("=" * 80)
    report.append("TOOL COUNT SIGNAL DISCRIMINATION DIAGNOSTIC - v3")
    report.append("=" * 80)
    report.append(f"Generated: {datetime.now().isoformat()}")
    report.append("")
    
    # Section 1: Prior Findings Summary
    report.append("-" * 80)
    report.append("SECTION 1: PRIOR DIAGNOSTIC FILES FOUND")
    report.append("-" * 80)
    if prior_findings:
        for filename, info in prior_findings.items():
            report.append(f"  • {filename}")
            # Extract key findings from content
            content = info['content'].upper()
            if 'ISSUE' in content or 'PROBLEM' in content or 'FINDING' in content:
                lines = info['content'].split('\n')
                key_lines = [l for l in lines if 'ISSUE' in l.upper() or 'PROBLEM' in l.upper() or 'FINDING' in l.upper()][:3]
                for kl in key_lines:
                    report.append(f"    - {kl.strip()[:100]}")
    else:
        report.append("  No prior diagnostic files found in project root.")
    report.append("")
    
    # Section 2: Score Distribution Analysis
    report.append("-" * 80)
    report.append("SECTION 2: SCORE DISTRIBUTION (mcp_signal_scores)")
    report.append("-" * 80)
    report.append(f"  Total records: {score_distribution['total_records']}")
    report.append(f"  Distinct score values: {score_distribution['distinct_score_values']}")
    report.append(f"  Score values: {score_distribution['distinct_scores']}")
    report.append(f"  Distribution:")
    for score, count in sorted(score_distribution['score_distribution'].items()):
        pct = score_distribution['score_percentages'][score]
        report.append(f"    Score {score}: {count} records ({pct}%)")
    report.append("")
    
    # Section 3: Raw Tool Count Distribution
    report.append("-" * 80)
    report.append("SECTION 3: RAW TOOL COUNT DISTRIBUTION (mcps table)")
    report.append("-" * 80)
    if 'error' not in tool_counts_analysis:
        report.append(f"  MCPs with tool_count: {tool_counts_analysis['total_mcps_with_tool_count']}")
        report.append(f"  Distinct tool count values: {tool_counts_analysis['distinct_tool_counts']}")
        report.append(f"  Range: {tool_counts_analysis['min_tool_count']} to {tool_counts_analysis['max_tool_count']}")
        report.append(f"  Distinct values: {tool_counts_analysis['distinct_values']}")
        report.append(f"  Distribution:")
        for count, freq in tool_counts_analysis['tool_count_distribution'].items():
            report.append(f"    {count} tools: {freq} MCPs")
    else:
        report.append(f"  ERROR: {tool_counts_analysis['error']}")
    report.append("")
    
    # Section 4: Score to Tool Count Mapping
    report.append("-" * 80)
    report.append("SECTION 4: SCORE -> TOOL COUNT MAPPING")
    report.append("-" * 80)
    if score_mapping:
        for score_str, mapping in score_mapping.items():
            report.append(f"  Score {score_str}:")
            report.append(f"    MCPs: {mapping['count_of_mcps']}")
            report.append(f"    Tool count range: {mapping['tool_count_min']} - {mapping['tool_count_max']}")
            report.append(f"    Average: {mapping['tool_count_avg']}")
            report.append(f"    Distinct tool counts: {mapping['tool_counts']}")
    report.append("")
    
    # Section 5: Bucket Analysis
    report.append("-" * 80)
    report.append("SECTION 5: BUCKET GRANULARITY ANALYSIS")
    report.append("-" * 80)
    if 'bucket_definitions' in bucket_analysis:
        report.append("  Bucket Definitions:")
        for bucket, range_val in bucket_analysis['bucket_definitions'].items():
            if isinstance(range_val, tuple):
                report.append(f"    {bucket}: {range_val[0]} - {range_val[1]}")
            else:
                report.append(f"    {bucket}: {range_val}")
        report.append("")
        report.append(f"  Bucket Alignment Rate: {bucket_analysis.get('alignment_rate', 'N/A')}%")
        report.append(f"  Aligned: {bucket_analysis.get('aligned_count', 'N/A')} / {bucket_analysis.get('total_evaluated', 'N/A')}")
        
        if bucket_analysis.get('misalignments'):
            report.append("")
            report.append("  Sample Misalignments:")
            for m in bucket_analysis['misalignments'][:5]:
                report.append(f"    {m['mcp_name']}: {m['tool_count']} tools -> Expected {m['expected_bucket']}, Got {m['actual_bucket']}")
    report.append("")
    
    # Section 6: Identified Issues
    report.append("-" * 80)
    report.append("SECTION 6: IDENTIFIED ISSUES")
    report.append("-" * 80)
    if issues:
        for i, issue in enumerate(issues, 1):
            report.append(f"  Issue {i}: [{issue['severity']}] {issue['type']}")
            report.append(f"    Description: {issue['description']}")
            if 'values' in issue:
                report.append(f"    Values: {issue['values']}")
            report.append(f"    Recommendation: {issue['recommendation']}")
            report.append("")
    else:
        report.append("  No critical issues identified.")
    report.append("")
    
    # Section 7: Sample Records
    report.append("-" * 80)
    report.append("SECTION 7: SAMPLE RECORDS (first 15)")
    report.append("-" * 80)
    for s in scores[:15]:
        report.append(f"  {s.get('mcp_name', 'Unknown'):30} | Tools: {str(s.get('tool_count', 'N/A')):6} | "
                     f"Score: {s.get('score', 'N/A')} | Bucket: {s.get('bucket', 'N/A')}")
    report.append("")
    
    # Section 8: Root Cause Analysis
    report.append("-" * 80)
    report.append("SECTION 8: ROOT CAUSE ANALYSIS")
    report.append("-" * 80)
    
    # Determine likely root cause
    if score_distribution['distinct_score_values'] == 2:
        if 'error' not in tool_counts_analysis and tool_counts_analysis['distinct_tool_counts'] >= 10:
            report.append("  ROOT CAUSE: Score calculation is too coarse")
            report.append("")
            report.append("  Evidence:")
            report.append(f"    • Tool count registry has {tool_counts_analysis['distinct_tool_counts']} distinct values")
            report.append(f"    • But only {score_distribution['distinct_score_values']} distinct scores")
            report.append("    • This indicates the scoring logic collapses variation")
            report.append("")
            report.append("  Possible causes:")
            report.append("    1. Score calculation uses binary threshold (e.g., 'has_tools' boolean)")
            report.append("    2. Score buckets are too wide (e.g., 0-100 maps to 0, 101-200 maps to 1)")
            report.append("    3. Score is derived from bucket name rather than count")
            report.append("    4. Normalization function has narrow output range")
        elif 'error' not in tool_counts_analysis:
            report.append("  ROOT CAUSE: Low variation in registry tool_count data")
            report.append("")
            report.append("  Evidence:")
            report.append(f"    • Registry has only {tool_counts_analysis['distinct_tool_counts']} distinct tool counts")
            report.append(f"    • Score discrimination limited by input data quality")
            report.append("")
            report.append("  Possible causes:")
            report.append("    1. Many MCPs have NULL or default tool_count values")
            report.append("    2. Tool counts not being properly extracted from MCP metadata")
            report.append("    3. Registry populated with synthetic/default data")
    
    report.append("")
    report.append("=" * 80)
    report.append("END OF DIAGNOSTIC REPORT")
    report.append("=" * 80)
    
    return "\n".join(report)


def main():
    """Main diagnostic execution."""
    logger.info("Starting Tool Count Signal Discrimination Diagnostic v3")
    
    # Read prior diagnostic files
    logger.info("Reading prior diagnostic files...")
    prior_findings = read_prior_diagnostics()
    
    try:
        # Connect to database
        logger.info("Connecting to database...")
        conn = connect_to_database()
        db_type = 'postgresql' if 'postgresql' in str(type(conn)) else 'sqlite'
        
        # Query data
        logger.info("Querying tool_count scores...")
        scores = query_tool_count_scores(conn, db_type)
        logger.info(f"  Found {len(scores)} score records")
        
        logger.info("Querying raw tool counts...")
        mcps = query_raw_tool_counts(conn, db_type)
        logger.info(f"  Found {len(mcps)} MCPs with tool_count")
        
        conn.close()
        
        # Perform analyses
        logger.info("Analyzing score distribution...")
        score_distribution = analyze_score_distribution(scores)
        
        logger.info("Analyzing tool count distribution...")
        tool_counts_analysis = analyze_tool_count_distribution(mcps)
        
        logger.info("Analyzing bucket granularity...")
        bucket_analysis = analyze_bucket_granularity(scores, tool_counts_analysis)
        
        logger.info("Analyzing score mapping...")
        score_mapping = analyze_score_to_tool_count_mapping(scores)
        
        logger.info("Identifying discrimination issues...")
        issues = identify_discrimination_issues(scores, tool_counts_analysis, score_mapping)
        
        # Generate report
        report = generate_diagnostic_report(
            scores, tool_counts_analysis, score_distribution,
            bucket_analysis, score_mapping, issues, prior_findings
        )
        
        # Output report
        print(report)
        
        # Save report to file
        report_path = PROJECT_ROOT / f"tool_count_discrimination_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_path, 'w') as f:
            f.write(report)
        logger.info(f"Report saved to: {report_path}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Diagnostic failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())