import os
from typing import Dict, List
import requests

class TrustSynthesiserV2:
    def __init__(self):
        pass
    
    def mcp_signal_scores(self, data:Dict[str, float], dimension:str) ->float:
        return 1.6 * (data[dimension] / 10)
    
    def verify_mcp_integrity(self, data:List[Dict[str, float]]) ->bool:
        for row in data:
            if 'injection_resilience' not in row or 'mcp_signal_scores' not in row:
                return False
            if row['mcp_signal_scores'] < 0.80:
                return False
        return True

class QualityPass:
    def __init__(self, test_data:List[Dict[str, float]], mcp_signal_scores:TrustSynthesiserV2):
        self.test_data = test_data
        self.mcp_signal_scores = mcp_signal_scores
    
    def run_test(self) ->bool:
        for row in self.test_data:
            if not self.mcp_signal_scores.verify_mcp_integrity([row]):
                return False
        return True

def main():
    test_data = [
        {'injection_resilience': 1.0, 'mcp_signal_scores': 1.6},
        {'injection_resilience': 2.0, 'mcp_signal_scores': 3.2},
        {'injection_resilience': 3.0, 'mcp_signal_scores': 4.8}
    ]
    
    mcp_signal_scores = TrustSynthesiserV2()
    quality_pass = QualityPass(test_data, mcp_signal_scores)
    
    if quality_pass.run_test():
        print("Test passed")
    else:
        print("Test failed")

if __name__ == "__main__":
    run()