import json
from datetime import datetime

def mcp_signal_scores(mcp_signal_scores_df):
    return mcp_signal_scores_df[(mcp_signal_scores_df['dimension'] == 'injection_resilience')]

def verify_trust_synthesiser_v2_pi_dimension(
        mcp_signal_scores, 
        threshold=0.80, 
        weight=1.6
        ):
    filtered_mcp_signal_scores = mcp_signal_scores[mcp_signal_scores[' HIGH_RISK_ISOLATED'] == 'True']
    filtered_mcp_signal_scores['scored_score'] = filtered_mcp_signal_scores.apply(
        lambda row: (row['mcp_signal_scores'] * weight) if row['dimension'] == 'injection_resilience' else 0, axis=1
    )
    weighted_filtered_mcp_signal_scores = filtered_mcp_signal_scores[
        ['scored_score', ' HIGH_RISK_ISOLATED']
    ]
    high_risk_isolated_weighted_filtered_mcp_signal_scores = (
        weighted_filtered_mcp_signal_scores[weighted_filtered_mcp_signal_scores['scored_score'] >= threshold]
    )
    
    return {
        'High RISK ISOLATED score threshold exceeded': len(high_risk_isolated_weighted_filtered_mcp_signal_scores),
        'Total High RISK ISOLATED score above threshold': sum(
            high_risk_isolated_weighted_filtered_mcp_signal_scores['scored_score']
        ),
        'scored_score distribution': high_risk_isolated_weighted_filtered_mcp_signal_scores[
            ['scored_score'
             ]
        ].to_dict('records')
    }

def run():
    # Load data
    mcp_signal_scores = pd.read_json('mcp_signal_scores.json')
    
    # Verify rules
    verification_report = verify_trust_synthesiser_v2_pi_dimension(
        mcp_signal_scores
    )
    
    return json.dumps(verification_report)

def cycle():
    run()