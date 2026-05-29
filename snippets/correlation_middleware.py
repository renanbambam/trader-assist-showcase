"""Correlation ID middleware — propagates a request ID through the entire async call stack.

ContextVar is used instead of threading.local because async coroutines don't run on
dedicated threads. threading.local would give a different value depending on which
worker thread happens to be executing the coroutine at any given moment.
ContextVar is tied to the async Task, so it stays consistent through every await.
"""

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject a correlation ID into every request's async context.

    Sources the ID from the incoming X-Correlation-ID header when present,
    otherwise generates a new UUID4. The same ID is echoed back in the
    response header so callers can correlate their own logs with server logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())

        # ContextVar.set() returns a Token for restoration — important in long-lived
        # ASGI workers where context from a previous request could otherwise leak.
        token = correlation_id_var.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)

        response.headers["X-Correlation-ID"] = correlation_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status code, and wall-clock duration."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        import time
        from loguru import logger

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        logger.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} "
            f"({duration_ms:.1f}ms) "
            f"[{correlation_id_var.get()[:8]}]"
        )
        return response
