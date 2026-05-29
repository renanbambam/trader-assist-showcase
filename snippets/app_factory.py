"""Application factory — create_app(settings).

Using a factory instead of a module-level app instance:
- Tests can pass explicit Settings without monkeypatching environment variables.
- The startup/shutdown lifecycle is explicit, testable, and colocated.
- Circular import issues with dependency injection are avoided.

Lifespan order:
  startup:  engine → session_factory → redis → event_bus → ws_gateway →
            run migrations → start broadcaster task → start scheduler
  shutdown: stop scheduler → cancel broadcaster → close redis → dispose engine
"""

import asyncio
import pathlib
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from oracle.config.settings import Settings, get_settings
from oracle.core.logging import setup_logging
from oracle.infrastructure.database.connection import build_engine, build_session_factory
from oracle.infrastructure.redis.client import build_redis_client
from oracle.infrastructure.redis.event_bus import RedisEventBus
from oracle.interface.api.middleware import CorrelationIdMiddleware, RequestLoggingMiddleware
from oracle.interface.websocket.broadcaster import EventBroadcaster
from oracle.interface.websocket.gateway import WebSocketGateway, router as ws_router


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    setup_logging(level=settings.LOG_LEVEL, serialize=settings.LOG_SERIALIZE)

    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    redis_client = build_redis_client(settings)
    event_bus = RedisEventBus(redis_client)
    ws_gateway = WebSocketGateway(max_connections=settings.WS_MAX_CONNECTIONS)
    broadcaster = EventBroadcaster(event_bus=event_bus, gateway=ws_gateway)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Run Alembic migrations in a thread pool — command.upgrade is synchronous.
        # Using run_in_executor avoids blocking the event loop during boot.
        if settings.ENVIRONMENT != "test":
            alembic_ini = pathlib.Path(__file__).resolve().parents[4] / "alembic.ini"
            if alembic_ini.exists():
                from alembic import command as alembic_cmd
                from alembic.config import Config as AlembicConfig
                cfg = AlembicConfig(str(alembic_ini))
                await asyncio.get_running_loop().run_in_executor(
                    None, alembic_cmd.upgrade, cfg, "head"
                )

        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.redis = redis_client
        app.state.event_bus = event_bus
        app.state.ws_gateway = ws_gateway

        # Broadcaster routes Redis pub/sub events to subscribed WebSocket clients.
        # Skipped in test environment — Redis is unavailable and the task would
        # cause teardown delays exceeding the pytest-asyncio lifespan timeout.
        broadcaster_task: asyncio.Task | None = None
        if settings.ENVIRONMENT != "test":
            broadcaster_task = asyncio.create_task(broadcaster.run())

        scheduler = None
        if settings.ENVIRONMENT != "test" and settings.SCHEDULER_ENABLED:
            try:
                scheduler = _build_and_start_scheduler(settings, engine, event_bus)
            except Exception:
                traceback.print_exc()
                logger.exception("Scheduler failed to start — continuing without it")

        logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} [{settings.ENVIRONMENT}]")
        yield

        if scheduler is not None:
            scheduler.shutdown()

        if broadcaster_task is not None:
            broadcaster_task.cancel()
            try:
                await broadcaster_task
            except asyncio.CancelledError:
                pass

        await redis_client.aclose()
        await engine.dispose()
        logger.info(f"{settings.APP_NAME} stopped")

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # Middleware executes in reverse registration order.
    # Actual execution: CORS → CorrelationId → RequestLogging → route handler
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from oracle.interface.api.routers import (
        analyses, analytics, briefing, chat, health, market, memory, replay, trades,
    )
    prefix = settings.API_V1_PREFIX
    app.include_router(health.router,     prefix=prefix)
    app.include_router(chat.router,       prefix=prefix)
    app.include_router(trades.router,     prefix=prefix)
    app.include_router(analyses.router,   prefix=prefix)
    app.include_router(memory.router,     prefix=prefix)
    app.include_router(analytics.router,  prefix=prefix)
    app.include_router(replay.router,     prefix=prefix)
    app.include_router(briefing.router,   prefix=prefix)
    app.include_router(market.router,     prefix=prefix)
    app.include_router(ws_router)

    frontend_dir = pathlib.Path(__file__).resolve().parents[4] / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")

    return app


def _build_and_start_scheduler(settings, engine, event_bus):
    from oracle.infrastructure.scheduler.scheduler import OracleScheduler
    from oracle.infrastructure.scheduler.jobs.morning_briefing_job import run_morning_briefing
    from oracle.infrastructure.scheduler.jobs.weekly_summary_job import run_weekly_summary
    from oracle.infrastructure.scheduler.jobs.news_refresh_job import run_news_refresh

    shared = {"engine": engine, "settings": settings, "event_bus": event_bus}
    scheduler = OracleScheduler(redis_url=settings.redis_url, timezone=settings.SCHEDULER_TIMEZONE)

    scheduler.add_cron_job(
        run_morning_briefing, job_id="morning_briefing",
        day_of_week="mon-fri", hour=7, minute=0, kwargs=shared,
    )
    scheduler.add_cron_job(
        run_weekly_summary, job_id="weekly_summary",
        day_of_week="mon", hour=8, minute=0, kwargs=shared,
    )
    scheduler.add_interval_job(
        run_news_refresh, job_id="news_refresh",
        minutes=30, kwargs={"settings": settings, "event_bus": event_bus},
    )

    scheduler.start()
    return scheduler
