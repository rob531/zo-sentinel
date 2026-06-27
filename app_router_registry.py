from fastapi import APIRouter
from typing import Dict
from pattern_learner import pattern_router

# Existing code
def register_router(router: APIRouter, name: str):
    # Implementation

def get_registered_routers() -> Dict[str, APIRouter]:
    # Implementation

# New code to register pattern_learner routes
if not any(router.name == "pattern_learner" for router in get_registered_routers().values()):
    register_router(pattern_router, "pattern_learner")
    # Add a marker comment to prevent re-integration
    # ZO-SENTINEL: pattern_learner routes registered