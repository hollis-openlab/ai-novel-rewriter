from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.i18n import parse_accept_language


class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started_at = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started_at) * 1000:.2f}"
        return response


class LanguageMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        header = request.headers.get("accept-language")
        request.state.lang = parse_accept_language(header)
        response = await call_next(request)
        return response


def install_core_middleware(app: Any) -> None:
    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(LanguageMiddleware)
