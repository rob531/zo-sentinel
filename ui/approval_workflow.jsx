from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn
import time
import requests

SERVICE_NAME = "approval_workflow"
PORT = 8780
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
INFERENCE_ROUTER_URL = "http://127.0.0.1:8773"

app = FastAPI(title="MCP Approval Workflow API")

class SubmitRequest(BaseModel):
    mcp_identifier: str = Field(..., description="MCP server name or URL")
    requester_name: str
    team: str
    business_purpose: str
    target_environment: str = Field(..., pattern="^(Production|Staging|Research|Development)$")

class DecisionRequest(BaseModel):
    submission_id: str
    decision: str = Field(..., pattern="^(APPROVE|CONDITIONAL|REJECT)$")
    conditions: Optional[str] = None
    notes: Optional[str] = None
    analyst_name: str
    expiry_days: Optional[int] = Field(default=90, ge=1, le=365)

class ApprovalWorkflow:
    def __init__(self):
        self.service_name = SERVICE_NAME
        self.write_url = WRITE_SERVICE_URL
        self.query_url = QUERY_SERVICE_URL
        self.inference_url = INFERENCE_ROUTER_URL

    def ws_write(self, table: str, rows: dict) -> bool:
        try:
            resp = requests.post(f"{self.write_url}/write", json={"table": table, "rows": rows, "wait": True}, timeout=10)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as e:
            print(f"ws_write error: {e}")
            return False

    def ws_query(self, sql: str) -> List[dict]:
        try:
            resp = requests.post(f"{self.query_url}/query", json={"sql": sql}, timeout=10)
            data = resp.json()
            return data.get("rows", [])
        except Exception as e:
            print(f"ws_query error: {e}")
            return []

    def ws_execute(self, sql: str) -> bool:
        try:
            resp = requests.post(f"{self.write_url}/execute", json={"sql": sql}, timeout=10)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as e:
            print(f"ws_execute error: {e}")
            return False

    def ensure_tables(self):
        tables = [
            "CREATE TABLE IF NOT EXISTS approval_submissions (submission_id VARCHAR PRIMARY KEY, mcp_identifier VARCHAR, requester_name VARCHAR, team VARCHAR, business_purpose VARCHAR, target_environment VARCHAR, status VARCHAR DEFAULT 'pending', submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS approval_decisions (decision_id VARCHAR PRIMARY KEY, submission_id VARCHAR, decision VARCHAR, conditions TEXT, notes TEXT, analyst_name VARCHAR, expiry_days INTEGER, decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ]
        for sql in tables:
            self.ws_execute(sql)

    def submit_for_assessment(self, data: SubmitRequest) -> dict:
        submission_id = f"SUB-{int(time.time())}-{data.mcp_identifier[:8]}"
        
        if not self.ws_write("approval_submissions", {
            "submission_id": submission_id,
            "mcp_identifier": data.mcp_identifier,
            "requester_name": data.requester_name,
            "team": data.team,
            "business_purpose": data.business_purpose,
            "target_environment": data.target_environment,
            "status": "pending"
        }):
            return {"success": False, "error": "Failed to save submission"}

        try:
            resp = requests.post(f"{self.inference_url}/assess", json={"server_id": data.mcp_identifier}, timeout=30)
            if resp.status_code == 200:
                assessment = resp.json()
                self.ws_write("approval_submissions", {
                    "submission_id": submission_id,
                    "status": "ready_for_review"
                })
                return {"success": True, "submission_id": submission_id, "assessment": assessment}
        except Exception as e:
            print(f"Assessment error: {e}")

        return {"success": True, "submission_id": submission_id, "assessment": None}

    def get_pending_submissions(self) -> List[dict]:
        return self.ws_query("SELECT * FROM approval_submissions WHERE status IN ('pending', 'ready_for_review') ORDER BY submitted_at DESC")

    def get_submission_details(self, submission_id: str) -> dict:
        rows = self.ws_query(f"SELECT * FROM approval_submissions WHERE submission_id = '{submission_id}'")
        if not rows:
            return {}
        submission = rows[0]
        
        signals = self.ws_query(f"SELECT * FROM signal_scores WHERE server_id = '{submission.get('mcp_identifier', '')}' ORDER BY scored_at DESC LIMIT 10")
        
        threat_rows = self.ws_query(f"SELECT * FROM mcp_threat_associations WHERE server_id = '{submission.get('mcp_identifier', '')}'")
        
        return {
            "submission": submission,
            "signals": signals,
            "threats": threat_rows
        }

    def submit_decision(self, data: DecisionRequest) -> dict:
        decision_id = f"DEC-{int(time.time())}"
        
        if not self.ws_write("approval_decisions", {
            "decision_id": decision_id,
            "submission_id": data.submission_id,
            "decision": data.decision,
            "conditions": data.conditions or "",
            "notes": data.notes or "",
            "analyst_name": data.analyst_name,
            "expiry_days": data.expiry_days
        }):
            return {"success": False, "error": "Failed to save decision"}

        self.ws_execute(f"UPDATE approval_submissions SET status = '{data.decision}' WHERE submission_id = '{data.submission_id}'")

        return {"success": True, "decision_id": decision_id}

    def get_registry(self) -> List[dict]:
        return self.ws_query("""
            SELECT s.server_id, s.name, s.verdict, s.trust_score, r.decision, r.decided_at, r.analyst_name
            FROM mcp_server_registry s
            LEFT JOIN (
                SELECT submission_id, decision, decided_at, analyst_name,
                       ROW_NUMBER() OVER (PARTITION BY submission_id ORDER BY decided_at DESC) as rn
                FROM approval_decisions
            ) r ON s.server_id = r.submission_id
            WHERE r.rn = 1 OR r.rn IS NULL
            ORDER BY COALESCE(r.decided_at, s.scan_count) DESC
        """)

workflow = ApprovalWorkflow()

@app.on_event("startup")
async def startup():
    workflow.ensure_tables()

@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "port": PORT}

@app.post("/api/submit")
async def submit(data: SubmitRequest):
    result = workflow.submit_for_assessment(data)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Submission failed"))
    return result

@app.post("/api/decision")
async def decision(data: DecisionRequest):
    result = workflow.submit_decision(data)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Decision failed"))
    return result

@app.get("/api/submissions")
async def get_submissions():
    return {"submissions": workflow.get_pending_submissions()}

@app.get("/api/submission/{submission_id}")
async def get_submission(submission_id: str):
    details = workflow.get_submission_details(submission_id)
    if not details:
        raise HTTPException(status_code=404, detail="Submission not found")
    return details

@app.get("/api/registry")
async def get_registry():
    return {"registry": workflow.get_registry()}

def run():
    uvicorn.run(app, host="127.0.0.1", port=PORT)

if __name__ == "__main__":
    run()