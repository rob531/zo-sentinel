class VerdictSummaryResponse(BaseModel):
        risk_tier_counts: dict[str, int]
        total_servers: int
        total_scored: int
        axis_avg_scores: dict[str, float]
        criteria_version: str
        generated_at: datetime