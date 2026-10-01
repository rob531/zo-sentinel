from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute

router = APIRouter(prefix="/api", tags=["axis_time_series"])

@router.get("/scores/")
async def get_scores(session = Depends(get_session)):
    scores = session.query(McpLlmAxisScore).all()
    return scores

@router.post("/disputes/")
async def create_dispute(dispute: McpScoreDispute, session = Depends(get_session)):
    session.add(dispute)
    session.commit()
    session.refresh(dispute)
    return dispute

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("router:router", host="0.0.0.0", port=8000, reload=True)