import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def build_signal_weights():
    import os
    
    SIGNAL_WEIGHTS = {
        'domain_trust': 0.20,
        'tool_description_safety': 0.20,
        'permission_scope': 0.15,
        'supply_chain': 0.15,
        'community_signal': 0.15,
        'temporal_stability': 0.15
    }
    
    SIGNAL_NAMES = ['domain_trust', 'tool_description_safety', 
                     'permission_scope', 'supply_chain',
                     'community_signal', 'temporal_stability']
    
    def compute_trust_score(signals_dict):
        return sum(SIGNAL_WEIGHTS[s] * signals_dict.get(s, 0) for s in SIGNAL_NAMES)
    
    def validate(signals_dict):
        assert round(compute_trust_score(signals_dict), 2) == 1.00, "Weights do not sum to 1.0"
        
    return {
        'SIGNAL_WEIGHTS': SIGNAL_WEIGHTS,
        'SIGNAL_NAMES': SIGNAL_NAMES,
        'compute_trust_score': compute_trust_score,
        'validate': validate
    }

if __name__ == '__main__':
    build_signal_weights()