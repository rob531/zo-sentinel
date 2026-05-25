import sys
import os
from datetime import datetime
from typing import Any
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from tool_description_safety_enrichment import compute_score, SERVICE_NAME, SERVICE_PORT, WRITE_SERVICE_URL, log, ws_write
except ImportError as e:
    print(f"ERROR: Cannot import tool_description_safety_enrichment: {e}")
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None


class ScoreDiscriminationAnalyzer:
    """Diagnoses why tool_description_safety_enrichment produces limited distinct scores."""
    
    def __init__(self):
        self.results = []
        self.score_buckets = {}
        self.discrimination_report = []
        
    def run_test_case(self, test_name: str, tool_def: dict[str, Any], expected_variation: str = "unknown") -> float:
        """Run a single test case and record the score."""
        try:
            score = compute_score(tool_def)
            self.results.append({
                'test_name': test_name,
                'expected_variation': expected_variation,
                'score': score,
                'tool_def': tool_def
            })
            bucket = round(score, 2)
            if bucket not in self.score_buckets:
                self.score_buckets[bucket] = []
            self.score_buckets[bucket].append(test_name)
            return score
        except Exception as e:
            self.discrimination_report.append(f"ERROR in {test_name}: {e}")
            return -1.0
    
    def test_entropy_variation(self) -> None:
        """Test varying description entropy (length, uniqueness of content)."""
        self.discrimination_report.append("\n=== ENTROPY VARIATION TESTS ===")
        
        base_params = {
            'name': 'test_tool',
            'description': 'A tool',
            'parameters': {
                'properties': {'input': {'type': 'string'}},
                'required': ['input']
            }
        }
        
        entropy_levels = [
            ('minimal', 'x'),
            ('short', 'A brief tool'),
            ('medium', 'This tool performs a specific function with parameters'),
            ('long', 'This is an extremely comprehensive and detailed tool description that explains every aspect of the functionality in great detail including edge cases, limitations, and detailed usage patterns'),
            ('very_long', 'A' * 1000),
            ('high_entropy', 'xyz abc def ghi jkl mno pqr stu vwx yz' * 10),
        ]
        
        prev_score = None
        for label, desc in entropy_levels:
            tool = base_params.copy()
            tool['description'] = desc
            score = self.run_test_case(f"entropy_{label}", tool, "should vary with description length/entropy")
            if prev_score is not None and score == prev_score:
                self.discrimination_report.append(f"  [CLIFF] entropy_{label}: score {score} == prev (no discrimination)")
            prev_score = score
    
    def test_parameter_documentation_variation(self) -> None:
        """Test varying parameter documentation coverage."""
        self.discrimination_report.append("\n=== PARAMETER DOCUMENTATION COVERAGE TESTS ===")
        
        base_tool = {
            'name': 'test_tool',
            'description': 'A test tool for parameter documentation analysis'
        }
        
        param_levels = [
            ('no_params', {}),
            ('empty_params', {'parameters': {}}),
            ('minimal_params', {'parameters': {'properties': {}}}),
            ('no_descriptions', {'parameters': {'properties': {'a': {'type': 'string'}}}}),
            ('some_descriptions', {'parameters': {'properties': {'a': {'type': 'string', 'description': 'param a'}}}}),
            ('all_described', {'parameters': {'properties': {'a': {'type': 'string', 'description': 'param a'}, 'b': {'type': 'integer', 'description': 'param b'}}, 'required': ['a', 'b']}}),
            ('with_defaults', {'parameters': {'properties': {'a': {'type': 'string', 'description': 'param a', 'default': 'test'}}}}),
        ]
        
        prev_score = None
        for label, extra in param_levels:
            tool = {**base_tool, **extra}
            score = self.run_test_case(f"param_{label}", tool, "should vary with doc coverage")
            if prev_score is not None and score == prev_score:
                self.discrimination_report.append(f"  [CLIFF] param_{label}: score {score} == prev (no discrimination)")
            prev_score = score
    
    def test_example_presence_variation(self) -> None:
        """Test varying example presence."""
        self.discrimination_report.append("\n=== EXAMPLE PRESENCE TESTS ===")
        
        base_tool = {
            'name': 'test_tool',
            'description': 'A test tool',
            'parameters': {'properties': {'x': {'type': 'string'}}}
        }
        
        example_levels = [
            ('no_examples', {}),
            ('empty_examples', {'examples': []}),
            ('single_example', {'examples': [{'x': 'value'}]}),
            ('multiple_examples', {'examples': [{'x': 'a'}, {'x': 'b'}, {'x': 'c'}]}),
            ('detailed_example', {'examples': [{'x': 'value', 'description': 'example usage'}]}),
        ]
        
        prev_score = None
        for label, extra in example_levels:
            tool = {**base_tool, **extra}
            score = self.run_test_case(f"example_{label}", tool, "should vary with example presence")
            if prev_score is not None and score == prev_score:
                self.discrimination_report.append(f"  [CLIFF] example_{label}: score {score} == prev (no discrimination)")
            prev_score = score
    
    def test_warning_tag_variation(self) -> None:
        """Test varying warning/security tags."""
        self.discrimination_report.append("\n=== WARNING TAG TESTS ===")
        
        base_tool = {
            'name': 'test_tool',
            'description': 'A test tool'
        }
        
        warning_levels = [
            ('no_tags', {}),
            ('generic_tags', {'tags': ['utility', 'data']}),
            ('security_tag', {'tags': ['security']}),
            ('warning_tag', {'tags': ['warning']}),
            ('danger_tag', {'tags': ['dangerous']}),
            ('multiple_risk_tags', {'tags': ['security', 'dangerous', 'warning']}),
            ('deprecated_tag', {'tags': ['deprecated']}),
            ('internal_tag', {'tags': ['internal']}),
        ]
        
        prev_score = None
        for label, extra in warning_levels:
            tool = {**base_tool, **extra}
            score = self.run_test_case(f"warning_{label}", tool, "should vary with risk indicators")
            if prev_score is not None and score == prev_score:
                self.discrimination_report.append(f"  [CLIFF] warning_{label}: score {score} == prev (no discrimination)")
            prev_score = score
    
    def test_version_tag_variation(self) -> None:
        """Test varying version tags."""
        self.discrimination_report.append("\n=== VERSION TAG TESTS ===")
        
        base_tool = {
            'name': 'test_tool',
            'description': 'A test tool'
        }
        
        version_levels = [
            ('no_version', {}),
            ('version_1', {'version': '1.0.0'}),
            ('version_2', {'version': '2.0.0'}),
            ('version_beta', {'version': '1.0.0-beta'}),
            ('version_major', {'version': '3.0.0'}),
            ('deprecate_field', {'deprecated': False}),
            ('deprecate_true', {'deprecated': True}),
        ]
        
        prev_score = None
        for label, extra in version_levels:
            tool = {**base_tool, **extra}
            score = self.run_test_case(f"version_{label}", tool, "should vary with version info")
            if prev_score is not None and score == prev_score:
                self.discrimination_report.append(f"  [CLIFF] version_{label}: score {score} == prev (no discrimination)")
            prev_score = score
    
    def test_combined_stress(self) -> None:
        """Test extreme combinations."""
        self.discrimination_report.append("\n=== COMBINED STRESS TESTS ===")
        
        stress_cases = [
            ('maximal', {
                'name': 'comprehensive_tool',
                'description': 'This is an extremely detailed and comprehensive tool description that explains every aspect of the functionality in exhaustive detail covering all parameters, edge cases, limitations, security considerations, and detailed usage patterns for experienced developers',
                'parameters': {
                    'properties': {
                        'input': {'type': 'string', 'description': 'The input string to process'},
                        'count': {'type': 'integer', 'description': 'Number of iterations'},
                        'options': {'type': 'object', 'description': 'Configuration options'}
                    },
                    'required': ['input']
                },
                'examples': [
                    {'input': 'test', 'count': 5},
                    {'input': 'example', 'count': 10, 'options': {'verbose': True}}
                ],
                'tags': ['production', 'stable'],
                'version': '3.2.1'
            }),
            ('minimal', {
                'name': 'x',
                'description': 'x'
            }),
            ('mid_quality', {
                'name': 'data_tool',
                'description': 'Process data with configurable options',
                'parameters': {'properties': {'data': {'type': 'string'}}}
            }),
            ('risky_no_docs', {
                'name': 'execute_command',
                'description': 'Execute a command',
                'tags': ['dangerous', 'security']
            }),
            ('risky_full_docs', {
                'name': 'execute_command',
                'description': 'Execute a command with full documentation including security considerations, input validation requirements, and usage examples',
                'parameters': {'properties': {'cmd': {'type': 'string', 'description': 'Command to execute'}, 'args': {'type': 'array', 'description': 'Command arguments'}}},
                'examples': [{'cmd': 'ls', 'args': ['-la']}],
                'tags': ['dangerous', 'security']
            }),
        ]
        
        for label, tool in stress_cases:
            score = self.run_test_case(f"stress_{label}", tool, "extreme variation expected")
    
    def analyze_discrimination_cliff(self) -> dict[str, Any]:
        """Identify where discrimination stops varying."""
        self.discrimination_report.append("\n=== DISCRIMINATION CLIFF ANALYSIS ===")
        
        distinct_scores = len(self.score_buckets)
        all_scores = [r['score'] for r in self.results if r['score'] >= 0]
        
        analysis = {
            'total_tests': len(self.results),
            'distinct_scores': distinct_scores,
            'score_range': (min(all_scores), max(all_scores)) if all_scores else (0, 0),
            'score_stddev': statistics.stdev(all_scores) if len(all_scores) > 1 else 0,
            'score_buckets': {str(k): v for k, v in self.score_buckets.items()},
            'cliffs_found': [],
            'factors_not_discriminating': []
        }
        
        for bucket_score, test_names in self.score_buckets.items():
            if len(test_names) > 3:
                self.discrimination_report.append(f"  CONCENTRATION: {len(test_names)} tests at score {bucket_score}: {test_names[:3]}...")
        
        test_by_category = {}
        for r in self.results:
            cat = r['test_name'].split('_')[0]
            if cat not in test_by_category:
                test_by_category[cat] = []
            test_by_category[cat].append(r['score'])
        
        for cat, scores in test_by_category.items():
            unique_scores = len(set(scores))
            if unique_scores <= 2:
                analysis['factors_not_discriminating'].append(f"{cat}: only {unique_scores} distinct scores")
                self.discrimination_report.append(f"  [ISSUE] {cat}: only {unique_scores} distinct score values across all variations")
        
        self.discrimination_report.append(f"\n  RESULT: Found {distinct_scores} distinct score values across {len(self.results)} test cases")
        self.discrimination_report.append(f"  Score range: {analysis['score_range'][0]:.3f} to {analysis['score_range'][1]:.3f}")
        self.discrimination_report.append(f"  Standard deviation: {analysis['score_stddev']:.4f}")
        
        return analysis
    
    def diagnose_root_cause(self) -> str:
        """Attempt to identify root cause of limited discrimination."""
        self.discrimination_report.append("\n=== ROOT CAUSE DIAGNOSIS ===")
        
        diagnosis = []
        
        all_scores = [r['score'] for r in self.results if r['score'] >= 0]
        if len(set(all_scores)) <= 4:
            diagnosis.append("SCORE COLLAPSE: Output appears to be bucketing into very few values")
            
            bucket_scores = sorted(set(all_scores))
            self.discrimination_report.append(f"  Score buckets observed: {bucket_scores}")
            
            if bucket_scores[0] == 0.0:
                diagnosis.append("  - Zero bucket present:某些因素导致最低分数")
            if bucket_scores[-1] == 1.0:
                diagnosis.append("  - One bucket present:某些因素导致最高分数")
            if len(bucket_scores) == 2:
                diagnosis.append("  - Binary output detected:可能是简单的阈值判断")
            if len(bucket_scores) == 3:
                diagnosis.append("  - Ternary output detected:可能是low/medium/high分类")
            if len(bucket_scores) == 4:
                diagnosis.append("  - Quaternary output detected:可能是0/0.33/0.67/1.0归一化")
        
        grouped_by_score = {}
        for r in self.results:
            score = round(r['score'], 2)
            if score not in grouped_by_score:
                grouped_by_score[score] = []
            grouped_by_score[score].append(r['test_name'])
        
        for score, tests in sorted(grouped_by_score.items()):
            if len(tests) >= len(self.results) * 0.3:
                diagnosis.append(f"  - {len(tests)}/{len(self.results)} tests converge to {score}: majority behavior")
        
        self.discrimination_report.append("\n  DIAGNOSIS:")
        for d in diagnosis:
            self.discrimination_report.append(f"    {d}")
        
        return "; ".join(diagnosis) if diagnosis else "Indeterminate"
    
    def run_full_diagnosis(self) -> dict[str, Any]:
        """Run complete diagnostic suite."""
        log(f"Starting discrimination diagnosis for compute_score()")
        
        self.test_entropy_variation()
        self.test_parameter_documentation_variation()
        self.test_example_presence_variation()
        self.test_warning_tag_variation()
        self.test_version_tag_variation()
        self.test_combined_stress()
        
        analysis = self.analyze_discrimination_cliff()
        diagnosis = self.diagnose_root_cause()
        
        report = {
            'timestamp': datetime.utcnow().isoformat(),
            'analysis': analysis,
            'diagnosis': diagnosis,
            'report_lines': self.discrimination_report,
            'all_results': [
                {'test': r['test_name'], 'score': r['score'], 'expected': r['expected_variation']}
                for r in self.results
            ]
        }
        
        full_report = "\n".join(self.discrimination_report)
        log(full_report)
        
        try:
            if requests:
                requests.post(
                    WRITE_SERVICE_URL,
                    json={
                        'table': 'tool_safety_diagnosis',
                        'rows': {
                            f"diagnostic_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}": {
                                'diagnosis_type': 'discrimination_analysis',
                                'distinct_scores': analysis['distinct_scores'],
                                'score_stddev': analysis['score_stddev'],
                                'root_cause': diagnosis,
                                'total_tests': analysis['total_tests'],
                                'report': full_report[:4000]
                            }
                        },
                        'wait': True
                    },
                    timeout=5
                )
        except Exception as e:
            log(f"Warning: Could not write diagnosis to DB: {e}")
        
        return report


SERVICE_NAME = "zo-sentinel-diagnosis"
SERVICE_PORT = 8773
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"


def log(msg: str) -> None:
    """Log message with timestamp."""
    print(f"[{datetime.utcnow().isoformat()}] {msg}", flush=True)


def run() -> None:
    """Main entry point."""
    log(f"ZO-SENTINEL Tool Description Safety Enrichment Discrimination Diagnostic")
    log(f"Testing: compute_score() from tool_description_safety_enrichment")
    
    analyzer = ScoreDiscriminationAnalyzer()
    report = analyzer.run_full_diagnosis()
    
    log(f"\nDIAGNOSIS COMPLETE")
    log(f"Distinct scores found: {report['analysis']['distinct_scores']}")
    log(f"Root cause: {report['diagnosis']}")
    
    if report['analysis']['distinct_scores'] <= 4:
        log("WARNING: Severe discrimination limitation detected - compute_score() produces <= 4 distinct values")
        log("This indicates the scoring function may use bucketing, thresholding, or lacks sensitivity")


if __name__ == '__main__':
    run()