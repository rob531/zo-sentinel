import sys
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Set
from collections import Counter
from itertools import product
import importlib.util

sys.path.insert(0, '/home/workspace/zo_sentinel')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger('zo_sentinel.diagnose_scope_enrichment')


class ScoreDiagnostics:
    
    FIELD_RANGES = {
        'registry_source': [0, 1, 2, 3],
        'age_days': list(range(0, 3650, 30)),
        'download_count': [0, 100, 1000, 10000, 100000, 1000000],
        'dependency_count': [0, 1, 5, 10, 50, 100],
        'publisher_verified': [0, 1],
        'stars': [0, 10, 100, 1000, 10000],
        'tool_count': [0, 1, 5, 10, 50, 100],
        'permission_flags': [0, 1, 2, 4, 8, 16, 32, 64, 128, 256]
    }
    
    def __init__(self):
        self.compute_score_func = None
        self.actual_weights = {}
        self.diagnostic_results = {}
        
    def load_compute_score(self) -> bool:
        spec = importlib.util.spec_from_file_location(
            "permission_scope_enrichment",
            "/home/workspace/zo_sentinel/permission_scope_enrichment.py"
        )
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
                if hasattr(module, 'compute_score'):
                    self.compute_score_func = module.compute_score
                    log.info("Successfully loaded compute_score function")
                    return True
                else:
                    log.error("compute_score function not found in module")
                    return False
            except Exception as e:
                log.error(f"Failed to load module: {e}")
                return False
        return False
    
    def load_weights_from_source(self) -> Dict[str, float]:
        try:
            with open('/home/workspace/zo_sentinel/permission_scope_enrichment.py', 'r') as f:
                source = f.read()
            
            import re
            weight_patterns = [
                r'(\w+)\s*\*=\s*([0-9.]+)',
                r'weight\s*\[\s*[\'"](\w+)[\'"]\s*\]\s*=\s*([0-9.]+)',
                r'(\w+)\s*:\s*([0-9.]+)'
            ]
            
            for pattern in weight_patterns:
                matches = re.findall(pattern, source)
                if matches:
                    for key, value in matches:
                        if key in self.FIELD_RANGES:
                            self.actual_weights[key] = float(value)
                            
            log.info(f"Extracted weights: {self.actual_weights}")
        except Exception as e:
            log.warning(f"Could not extract weights: {e}")
            
        return self.actual_weights
    
    def generate_synthetic_corpus(self, samples_per_field: int = 20) -> List[Dict]:
        corpus = []
        
        field_values = {}
        for field, values in self.FIELD_RANGES.items():
            step = max(1, len(values) // samples_per_field)
            field_values[field] = values[::step]
        
        keys = list(field_values.keys())
        for combo in product(*[field_values[k] for k in keys]):
            row = dict(zip(keys, combo))
            corpus.append(row)
            
        log.info(f"Generated {len(corpus)} synthetic samples")
        return corpus
    
    def run_scoring_simulation(self, corpus: List[Dict]) -> Tuple[List[float], Counter]:
        if not self.compute_score_func:
            log.error("No compute_score function available")
            return [], Counter()
            
        scores = []
        errors = 0
        
        for row in corpus:
            try:
                score = self.compute_score_func(
                    registry_source=row.get('registry_source', 0),
                    age_days=row.get('age_days', 0),
                    download_count=row.get('download_count', 0),
                    dependency_count=row.get('dependency_count', 0),
                    publisher_verified=row.get('publisher_verified', 0),
                    stars=row.get('stars', 0),
                    tool_count=row.get('tool_count', 0),
                    permission_flags=row.get('permission_flags', 0)
                )
                scores.append(score)
            except Exception as e:
                errors += 1
                log.debug(f"Scoring error: {e}")
                
        score_counts = Counter(scores)
        log.info(f"Scored {len(scores)} samples, {errors} errors, {len(score_counts)} distinct scores")
        
        return scores, score_counts
    
    def analyze_score_distribution(self, scores: List[float]) -> Dict[str, Any]:
        if not scores:
            return {}
            
        sorted_scores = sorted(scores)
        n = len(sorted_scores)
        
        min_score = min(sorted_scores)
        max_score = max(sorted_scores)
        median = sorted_scores[n // 2]
        
        unique_scores = sorted(set(sorted_scores))
        distinct_count = len(unique_scores)
        
        if distinct_count > 1:
            score_range = max_score - min_score
            compression_ratio = score_range / distinct_count if distinct_count > 0 else 0
        else:
            score_range = 0
            compression_ratio = 0
            
        analysis = {
            'total_samples': n,
            'distinct_scores': distinct_count,
            'min_score': min_score,
            'max_score': max_score,
            'score_range': score_range,
            'median': median,
            'unique_values': unique_scores,
            'compression_ratio': compression_ratio
        }
        
        return analysis
    
    def identify_score_collisions(self, corpus: List[Dict], scores: List[float]) -> List[Dict]:
        score_to_rows = {}
        
        for i, score in enumerate(scores):
            if score not in score_to_rows:
                score_to_rows[score] = []
            score_to_rows[score].append(corpus[i])
            
        collisions = []
        
        for score, rows in score_to_rows.items():
            if len(rows) > 1:
                input_vectors = [tuple(sorted(r.items())) for r in rows]
                unique_vectors = set(input_vectors)
                
                collisions.append({
                    'score': score,
                    'total_rows': len(rows),
                    'unique_input_vectors': len(unique_vectors),
                    'collision_count': len(rows) - len(unique_vectors),
                    'sample_inputs': rows[:3]
                })
                
        collisions.sort(key=lambda x: x['collision_count'], reverse=True)
        
        return collisions
    
    def diagnose_low_diversity(self, analysis: Dict, collisions: List[Dict], 
                               weights: Dict) -> Dict[str, Any]:
        diagnosis = {
            'root_causes': [],
            'weight_analysis': {},
            'field_contribution_analysis': {},
            'recommendations': []
        }
        
        distinct = analysis.get('distinct_scores', 0)
        
        if distinct <= 4:
            diagnosis['root_causes'].append({
                'cause': 'EXTREMELY_LOW_DIVERSITY',
                'severity': 'CRITICAL',
                'detail': f'Only {distinct} distinct scores produced from {analysis["total_samples"]} samples',
                'expected': 'Hundreds to thousands of distinct scores expected',
                'actual': f'{distinct} scores',
                'gap': f'{(1 - distinct / 1000) * 100:.1f}% score compression'
            })
        
        for weight_key, weight_value in weights.items():
            if weight_value == 0:
                diagnosis['weight_analysis'][weight_key] = {
                    'weight': weight_value,
                    'status': 'ZERO_WEIGHT',
                    'impact': 'This field contributes 0 to score - potential data loss'
                }
                
        if weights:
            weight_values = list(weights.values())
            max_weight = max(weight_values) if weight_values else 1
            min_weight = min(weight_values) if weight_values else 0
            
            if max_weight > 0 and min_weight / max_weight < 0.01:
                diagnosis['root_causes'].append({
                    'cause': 'DOMINANT_WEIGHT_IMBALANCE',
                    'severity': 'HIGH',
                    'detail': 'One weight dominates all others',
                    'max_weight': max_weight,
                    'min_weight': min_weight,
                    'ratio': min_weight / max_weight if max_weight > 0 else 0
                })
                
        for field, values in self.FIELD_RANGES.items():
            field_weights = [v for k, v in weights.items() if field in k]
            if field_weights and field_weights[0] == 0:
                diagnosis['field_contribution_analysis'][field] = {
                    'impact': 'ZERO',
                    'affected_samples': analysis['total_samples']
                }
                
        high_collision_collisions = [c for c in collisions if c['collision_count'] > 10]
        if high_collision_collisions:
            diagnosis['root_causes'].append({
                'cause': 'HIGH_COLLISION_RATE',
                'severity': 'MEDIUM',
                'detail': f'{len(high_collision_collisions)} score buckets have >10 collisions',
                'examples': [
                    {
                        'score': c['score'],
                        'collision_count': c['collision_count'],
                        'unique_inputs': c['unique_input_vectors']
                    }
                    for c in high_collision_collisions[:3]
                ]
            })
            
        if distinct <= 4:
            diagnosis['recommendations'].extend([
                'Review compute_score function for integer division/truncation',
                'Check if score is being clamped to fixed buckets',
                'Verify weight values are not all zeros',
                'Ensure floating point precision is maintained',
                'Consider logarithmic scaling for large numeric fields'
            ])
            
        return diagnosis
    
    def analyze_field_sensitivity(self, corpus: List[Dict], scores: List[float],
                                  weights: Dict) -> Dict[str, Any]:
        field_sensitivity = {}
        
        for field in self.FIELD_RANGES.keys():
            field_indices = [i for i, row in enumerate(corpus) if field in row]
            
            if not field_indices:
                continue
                
            field_scores = [(corpus[i][field], scores[i]) for i in field_indices]
            
            if len(field_scores) > 1:
                unique_inputs = set(s[0] for s in field_scores)
                unique_outputs = set(s[1] for s in field_scores)
                
                field_sensitivity[field] = {
                    'unique_input_values': len(unique_inputs),
                    'unique_output_scores': len(unique_outputs),
                    'sensitivity_ratio': len(unique_outputs) / len(unique_inputs) if unique_inputs else 0,
                    'weight': weights.get(field, 0),
                    'status': 'ACTIVE' if weights.get(field, 0) > 0 else 'MUTED'
                }
                
        return field_sensitivity
    
    def generate_report(self, analysis: Dict, collisions: List[Dict],
                       diagnosis: Dict, sensitivity: Dict) -> str:
        
        report_lines = [
            "=" * 70,
            "ZO-SENTINEL: Permission Scope Enrichment Score Diagnostic Report",
            "=" * 70,
            f"Generated: {datetime.utcnow().isoformat()}Z",
            "",
            "-" * 70,
            "SECTION 1: SCORE DISTRIBUTION ANALYSIS",
            "-" * 70,
            f"  Total samples tested:     {analysis.get('total_samples', 0):,}",
            f"  Distinct scores:          {analysis.get('distinct_scores', 0)}",
            f"  Minimum score:            {analysis.get('min_score', 0)}",
            f"  Maximum score:            {analysis.get('max_score', 0)}",
            f"  Score range:              {analysis.get('score_range', 0)}",
            f"  Median score:             {analysis.get('median', 0)}",
            f"  Compression ratio:        {analysis.get('compression_ratio', 0):.4f}",
            "",
            f"  Unique score values:",
        ]
        
        for val in analysis.get('unique_values', []):
            report_lines.append(f"    - {val}")
            
        report_lines.extend([
            "",
            "-" * 70,
            "SECTION 2: ROOT CAUSE ANALYSIS",
            "-" * 70,
        ])
        
        if not diagnosis.get('root_causes'):
            report_lines.append("  No critical issues identified")
        else:
            for cause in diagnosis['root_causes']:
                report_lines.extend([
                    f"",
                    f"  [{cause['severity']}] {cause['cause']}",
                    f"    Detail: {cause['detail']}",
                ])
                if 'max_weight' in cause:
                    report_lines.append(f"    Max weight: {cause['max_weight']}")
                    report_lines.append(f"    Min weight: {cause['min_weight']}")
                    report_lines.append(f"    Ratio: {cause['ratio']:.6f}")
                if 'examples' in cause:
                    report_lines.append(f"    Examples:")
                    for ex in cause['examples']:
                        report_lines.append(f"      - Score {ex['score']}: {ex['collision_count']} collisions, {ex['unique_inputs']} unique inputs")
                        
        report_lines.extend([
            "",
            "-" * 70,
            "SECTION 3: FIELD SENSITIVITY ANALYSIS",
            "-" * 70,
            "",
            "  Field              Weight      Unique In   Unique Out  Sensitivity  Status",
            "  " + "-" * 70,
        ])
        
        for field, data in sorted(sensitivity.items()):
            report_lines.append(
                f"  {field:<18} {data['weight']:<10.4f} {data['unique_input_values']:<10} "
                f"{data['unique_output_scores']:<10} {data['sensitivity_ratio']:<10.4f} {data['status']}"
            )
            
        zero_weight_fields = [f for f, d in sensitivity.items() if d['weight'] == 0]
        muted_fields = [f for f, d in sensitivity.items() if d['status'] == 'MUTED']
        
        report_lines.extend([
            "",
            "-" * 70,
            "SECTION 4: SCORE COLLISION ANALYSIS",
            "-" * 70,
            f"  Total collision buckets: {len(collisions)}",
        ])
        
        if collisions:
            report_lines.extend([
                "",
                "  Top 5 collision buckets:",
                "",
                "  Score        Collisions    Unique Inputs    Sample Inputs",
                "  " + "-" * 60,
            ])
            
            for c in collisions[:5]:
                sample_str = str(c['sample_inputs'][:1])[:40]
                report_lines.append(
                    f"  {c['score']:<12} {c['collision_count']:<12} "
                    f"{c['unique_input_vectors']:<14} {sample_str}..."
                )
                
        report_lines.extend([
            "",
            "-" * 70,
            "SECTION 5: WEIGHT ANALYSIS",
            "-" * 70,
        ])
        
        if diagnosis.get('weight_analysis'):
            for field, data in diagnosis['weight_analysis'].items():
                report_lines.append(f"  {field}: weight={data['weight']}, status={data['status']}")
        else:
            report_lines.append("  No zero-weight fields detected")
            
        report_lines.extend([
            "",
            "-" * 70,
            "SECTION 6: RECOMMENDATIONS",
            "-" * 70,
        ])
        
        if diagnosis.get('recommendations'):
            for i, rec in enumerate(diagnosis['recommendations'], 1):
                report_lines.append(f"  {i}. {rec}")
        else:
            report_lines.append("  No specific recommendations - score distribution appears normal")
            
        report_lines.extend([
            "",
            "=" * 70,
            "END OF REPORT",
            "=" * 70,
        ])
        
        return "\n".join(report_lines)
    
    def run(self) -> Dict[str, Any]:
        log.info("Starting permission scope enrichment diagnostic")
        
        corpus = self.generate_synthetic_corpus(samples_per_field=15)
        
        if self.compute_score_func:
            scores, score_counts = self.run_scoring_simulation(corpus)
        else:
            log.error("Cannot run simulation without compute_score function")
            scores = []
            score_counts = Counter()
            
        analysis = self.analyze_score_distribution(scores)
        
        weights = self.load_weights_from_source()
        
        collisions = self.identify_score_collisions(corpus, scores) if scores else []
        
        diagnosis = self.diagnose_low_diversity(analysis, collisions, weights)
        
        sensitivity = self.analyze_field_sensitivity(corpus, scores, weights)
        
        report = self.generate_report(analysis, collisions, diagnosis, sensitivity)
        
        self.diagnostic_results = {
            'analysis': analysis,
            'diagnosis': diagnosis,
            'sensitivity': sensitivity,
            'collision_count': len(collisions),
            'report': report
        }
        
        log.info(f"Diagnostic complete. Distinct scores: {analysis.get('distinct_scores', 0)}")
        
        return self.diagnostic_results


def main():
    diag = ScoreDiagnostics()
    
    diag.load_compute_score()
    
    results = diag.run()
    
    print("\n" + results['report'])
    
    output_file = '/home/workspace/zo_sentinel/diagnostic_reports/permission_scope_enrichment_diagnostic.json'
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump({
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'summary': {
                'distinct_scores': results['analysis'].get('distinct_scores', 0),
                'total_samples': results['analysis'].get('total_samples', 0),
                'root_causes_found': len(results['diagnosis'].get('root_causes', [])),
                'recommendations_count': len(results['diagnosis'].get('recommendations', []))
            },
            'analysis': results['analysis'],
            'diagnosis': results['diagnosis'],
            'sensitivity': results['sensitivity'],
            'collision_count': results['collision_count']
        }, f, indent=2, default=str)
    
    log.info(f"Diagnostic results saved to {output_file}")
    
    return results


if __name__ == '__main__':
    main()