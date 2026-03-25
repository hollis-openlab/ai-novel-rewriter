from fastapi import APIRouter

from backend.app.api.routes import (
    artifacts_router,
    config_router,
    chapters_router,
    health_router,
    novels_router,
    split_rules_router,
    providers_router,
    stages_router,
    workers_router,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(novels_router)
api_router.include_router(chapters_router)
api_router.include_router(config_router)
api_router.include_router(split_rules_router)
api_router.include_router(providers_router)
api_router.include_router(workers_router)
api_router.include_router(artifacts_router)
api_router.include_router(stages_router)
