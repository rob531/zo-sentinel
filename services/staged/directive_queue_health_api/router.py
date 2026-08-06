from fastapi import APIRouter, Depends
from app.db import get_session
from .logic import get_directive_queue_health

router = APIRouter(prefix="/api")


@router.get("/directives/health", dependencies=[Depends(get_session)])
def health():
    """
    Returns health information about the directive queue.
    """
    return get_directive_queue_health()


if __name__ == "__main__":
    import os
    import json
    import tempfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Create a temporary filesystem layout that the logic layer expects
    with tempfile.TemporaryDirectory() as tmp_dir:
        pending_dir = os.path.join(tmp_dir, "directives", "pending")
        proposed_dir = os.path.join(tmp_dir, "directives", "proposed")
        os.makedirs(pending_dir, exist_ok=True)
        os.makedirs(proposed_dir, exist_ok=True)

        # Seed a few dummy JSON files
        for i in range(2):
            with open(os.path.join(pending_dir, f"pending_{i}.json"), "w") as f:
                json.dump({"id": i}, f)

        for i in range(1):
            with open(os.path.join(proposed_dir, f"proposed_{i}.json"), "w") as f:
                json.dump({"id": i}, f)

        # Switch cwd so the logic module sees the temporary layout
        original_cwd = os.getcwd()
        os.chdir(tmp_dir)
        try:
            # Build a minimal FastAPI app that includes this router
            app = FastAPI()
            app.include_router(router)

            # Override the DB session dependency with a dummy placeholder
            def _dummy_session():
                class _Dummy:
                    pass

                return _Dummy()

            app.dependency_overrides[get_session] = _dummy_session

            client = TestClient(app)
            response = client.get("/api/directives/health")
            payload = response.json()

            assert response.status_code == 200
            assert isinstance(payload.get("pending_count"), int)
            assert isinstance(payload.get("healthy"), bool)

            print("PASS")
        finally:
            os.chdir(original_cwd)