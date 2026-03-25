from fastapi import APIRouter

from backend.app.api.schemas import HealthResponse
from backend.app.core.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(app_name=settings.app_name, version=settings.app_version)
