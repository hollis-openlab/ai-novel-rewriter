from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.schemas import WorkerStatusResponse
from backend.app.core.errors import AppError, ErrorCode
from backend.app.services.worker_pool import WorkerPool

router = APIRouter(prefix="/workers", tags=["workers"])


class WorkerCountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=50)


@router.get("/status", response_model=WorkerStatusResponse)
async def get_worker_status(request: Request) -> WorkerStatusResponse:
    worker_pool = _get_worker_pool(request)
    if worker_pool is None:
        return WorkerStatusResponse(active=0, idle=0, queue_size=0)

    status = worker_pool.status()
    return WorkerStatusResponse(
        active=status.active_workers,
        idle=status.idle_workers,
        queue_size=status.queue_size,
    )


@router.put("/count")
async def set_worker_count(request: Request, payload: WorkerCountRequest) -> dict[str, int]:
    worker_pool = _get_worker_pool(request)
    if worker_pool is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "Worker pool is not initialized", status_code=500)

    await worker_pool.set_worker_count(payload.count)
    return {"count": payload.count}


def _get_worker_pool(request: Request) -> WorkerPool | None:
    return getattr(request.app.state, "worker_pool", None)
