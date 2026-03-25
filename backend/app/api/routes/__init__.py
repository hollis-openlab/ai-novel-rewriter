from backend.app.api.routes.artifacts import router as artifacts_router
from backend.app.api.routes.config import router as config_router
from backend.app.api.routes.chapters import router as chapters_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.novels import router as novels_router
from backend.app.api.routes.providers import router as providers_router
from backend.app.api.routes.split_rules import router as split_rules_router
from backend.app.api.routes.stages import router as stages_router
from backend.app.api.routes.workers import router as workers_router

__all__ = [
    "artifacts_router",
    "config_router",
    "chapters_router",
    "health_router",
    "novels_router",
    "providers_router",
    "split_rules_router",
    "stages_router",
    "workers_router",
]
