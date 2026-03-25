from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.api.router import api_router
from backend.app.api.routes.ws import router as ws_router
from backend.app.api.schemas import HealthResponse
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode, error_payload
from backend.app.core.middleware import install_core_middleware
from backend.app.core.prompt_templates import PromptTemplateRegistry
from backend.app.core.settings import get_settings
from backend.app.db import dispose_engine, get_engine, init_db
from backend.app.llm.rate_limit import ProviderRateLimitManager
from backend.app.llm.audit_log import PromptAuditLogger
from backend.app.services.worker_pool import WorkerPool
from backend.app.services.ws_hub import WsHub

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ArtifactStore(settings.data_dir)
    store.ensure_base_dirs()
    db_engine = get_engine()
    await init_db(db_engine)
    worker_pool = WorkerPool(rate_limit_manager=ProviderRateLimitManager())
    await worker_pool.start()
    app.state.settings = settings
    app.state.artifact_store = store
    app.state.prompt_templates = PromptTemplateRegistry()
    app.state.db_engine = db_engine
    app.state.worker_pool = worker_pool
    app.state.prompt_audit_logger = PromptAuditLogger()
    app.state.ws_hub = WsHub()
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    settings.openapi_output_path.parent.mkdir(parents=True, exist_ok=True)
    yield
    await worker_pool.close()
    await dispose_engine(db_engine)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_core_middleware(app)
app.include_router(ws_router)
app.include_router(api_router)


@app.get("/health", response_model=HealthResponse)
async def root_health() -> HealthResponse:
    return HealthResponse(app_name=settings.app_name, version=settings.app_version)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"message": "AI Novel Backend"}


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, **exc.details),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_payload(
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            errors=exc.errors(),
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL_ERROR
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(code, exc.detail if isinstance(exc.detail, str) else "HTTP error"),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_payload(ErrorCode.INTERNAL_ERROR, "Internal server error", exception=exc.__class__.__name__),
    )
