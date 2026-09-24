# services/staged/signal_evidence_export/logic.py
from typing import List, Dict, Any
from sqlalchemy.orm import Session


def get_signal_evidence(server_id: str, session: Session) -> Dict[str, Any]:
    """
    Read signal evidence from mcp_signal_scores and mcp_signal_enrichments tables.
    Returns {server_id, signals: [{type, confidence, evidence_blob}]} as JSON.
    """
    signals = []
    
    # Query mcp_signal_scores for signal data
    scores_result = session.execute(
        f"SELECT signal_type, confidence FROM mcp_signal_scores WHERE server_id = :server_id",
        {"server_id": server_id}
    ).fetchall()
    
    # Query mcp_signal_enrichments for evidence blobs
    enrichments_result = session.execute(
        f"SELECT signal_type, evidence_blob FROM mcp_signal_enrichments WHERE server_id = :server_id",
        {"server_id": server_id}
    ).fetchall()
    
    # Build enrichment lookup
    enrichment_map = {row[0]: row[1] for row in enrichments_result}
    
    # Combine scores with enrichments
    for row in scores_result:
        signal_type = row[0]
        confidence = row[1]
        evidence_blob = enrichment_map.get(signal_type, {})
        
        signals.append({
            "type": signal_type,
            "confidence": confidence,
            "evidence_blob": evidence_blob
        })
    
    return {
        "server_id": server_id,
        "signals": signals
    }