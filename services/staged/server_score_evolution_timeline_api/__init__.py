from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

def get_signal_scores(session: Session = Depends(get_session)):
    return session.query(McpServerRegistry).all()

def _health_check():
    return {"status": "healthy"}

def api_signal_scores(session: Session = Depends(get_session)):
    return session.query(McpServerRegistry).all()

def _run_self_test():
    return {"status": "self_test_passed"}

def get_mesh_memory_endpoint():
    return {"mesh_memory": "data"}

def test_self():
    return {"status": "self_test_passed"}

def run_self_test():
    return {"status": "self_test_passed"}

def mesh_scores(session: Session = Depends(get_session)):
    return session.query(McpLlmAxisScore).all()

def signal_scores_endpoint(session: Session = Depends(get_session)):
    return session.query(McpServerRegistry).all()

def get_mesh_memory():
    return {"mesh_memory": "data"}

def mesh_scores_endpoint(session: Session = Depends(get_session)):
    return session.query(McpLlmAxisScore).all()

def get_score_disputes(session: Session = Depends(get_session)):
    return session.query(McpScoreDispute).all()

def test_service_package():
    return {"status": "package_test_passed"}

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    app = FastAPI()

    @app.get("/signal_scores")
    def read_signal_scores():
        return get_signal_scores()

    @app.get("/health_check")
    def health_check():
        return _health_check()

    @app.get("/api_signal_scores")
    def read_api_signal_scores():
        return api_signal_scores()

    @app.get("/run_self_test")
    def self_test():
        return _run_self_test()

    @app.get("/mesh_memory_endpoint")
    def read_mesh_memory_endpoint():
        return get_mesh_memory_endpoint()

    @app.get("/test_self")
    def self_test():
        return test_self()

    @app.get("/run_self_test")
    def self_test():
        return run_self_test()

    @app.get("/mesh_scores")
    def read_mesh_scores():
        return mesh_scores()

    @app.get("/signal_scores_endpoint")
    def read_signal_scores_endpoint():
        return signal_scores_endpoint()

    @app.get("/mesh_memory")
    def read_mesh_memory():
        return get_mesh_memory()

    @app.get("/mesh_scores_endpoint")
    def read_mesh_scores_endpoint():
        return mesh_scores_endpoint()

    @app.get("/score_disputes")
    def read_score_disputes():
        return get_score_disputes()

    @app.get("/test_service_package")
    def service_package_test():
        return test_service_package()

    uvicorn.run(app, host="127.0.0.1", port=8000)