results = db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.id).label('count'),
            func.avg(McpLlmAxisScore.score).label('avg_score')
        ).join(McpLlmAxisScore, McpServerRegistry.id == McpLlmAxisScore.server_id)\
         .group_by(McpServerRegistry.risk_tier).all()