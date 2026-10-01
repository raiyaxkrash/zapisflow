"""FastAPI webhook ingestion engine for multi-tenant Telegram bots.

Receives updates from Telegram for multiple BotInstances, validates secret tokens in constant time,
enforces payload size limits, deduplicates updates atomically in Redis, retrieves bots via BotRegistry,
and dispatches them with strict tenant context.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import secrets
from typing import Any, AsyncGenerator, Optional
import uuid

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.bot_instance import create_dispatcher
from app.config.settings import settings
from app.core.security import install_sensitive_logging
from app.database.models.master import BotInstanceStatus
from app.database.session import async_session_factory, close_db, engine, init_db
from app.manager_bot.dispatcher import create_manager_dispatcher
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.scheduler import MultiTenantScheduler
from app.services.bot_registry import BotRegistry
from app.services.exceptions import (
    BotDisabledError,
    BotNotFoundError,
    BotProvisioningError,
    BotRegistryError,
    BotUnavailableError,
)
from app.services.update_dedup import (
    DedupState,
    DedupUnavailableError,
    UpdateDeduplicator,
)

logger = logging.getLogger("app.web.app")


async def read_limited_request_body(request: Request, max_bytes: int) -> bytes:
    """Safely stream and read request body up to max_bytes without buffering unbounded streams."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Payload Too Large",
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Payload Too Large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages application lifecycle: DB initialization, Redis, BotRegistry and Dispatcher."""
    install_sensitive_logging()
    logger.info("Starting Multi-Bot Webhook Ingestion Engine...")

    # Strict production configuration check (fail fast on invalid config)
    settings.validate_production_configuration()

    # Initialize Database if not already done
    await init_db()

    # Initialize Redis client if not explicitly injected
    if not hasattr(app.state, "redis_client") or app.state.redis_client is None:
        try:
            redis_client = Redis.from_url(settings.redis_url)
            await redis_client.ping()
            app.state.redis_client = redis_client
            logger.info("Connected to Redis at %s:%s", settings.redis_host, settings.redis_port)
        except Exception as e:
            if settings.is_production:
                logger.critical("Fatal: Cannot connect to Redis in production: %s", e)
                raise RuntimeError(f"Redis is required in production environment: {e}")
            logger.warning("Could not connect to Redis (%s), running with fallback", e)
            app.state.redis_client = None

    # Initialize session factory if not explicitly injected
    if not hasattr(app.state, "session_factory") or app.state.session_factory is None:
        app.state.session_factory = async_session_factory

    # Initialize BotRegistry if not explicitly injected
    if not hasattr(app.state, "registry") or app.state.registry is None:
        registry = BotRegistry(
            redis_client=app.state.redis_client,
            max_size=settings.bot_registry_cache_max_size,
            ttl_seconds=settings.bot_registry_cache_ttl_seconds,
        )
        app.state.registry = registry
    await app.state.registry.start_invalidation_listener()

    # Initialize UpdateDeduplicator if not explicitly injected
    if not hasattr(app.state, "deduplicator") or app.state.deduplicator is None:
        app.state.deduplicator = UpdateDeduplicator(
            redis_client=app.state.redis_client,
            ttl_completed=settings.webhook_update_dedup_ttl,
        )

    # Initialize Dispatcher if not explicitly injected
    if not hasattr(app.state, "dp") or app.state.dp is None:
        app.state.dp = await create_dispatcher()

    # Initialize Manager Dispatcher if not explicitly injected
    if not hasattr(app.state, "manager_dp") or app.state.manager_dp is None:
        app.state.manager_dp = await create_manager_dispatcher(redis_client=app.state.redis_client)

    # Initialize Manager Bot if not explicitly injected and token configured
    if not hasattr(app.state, "manager_bot") or app.state.manager_bot is None:
        if settings.manager_bot_token:
            app.state.manager_bot = Bot(
                token=settings.manager_bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )

    # Initialize MultiTenantScheduler if enabled and not explicitly injected
    if not hasattr(app.state, "scheduler") or app.state.scheduler is None:
        if settings.scheduler_enabled:
            scheduler = MultiTenantScheduler(
                registry=app.state.registry,
                session_maker=app.state.session_factory,
            )
            scheduler.start()
            app.state.scheduler = scheduler
            logger.info("MultiTenantScheduler started in webhook application")
        else:
            app.state.scheduler = None

    yield

    logger.info("Shutting down Multi-Bot Webhook Ingestion Engine...")
    if hasattr(app.state, "scheduler") and app.state.scheduler:
        try:
            app.state.scheduler.shutdown(wait=False)
            logger.info("MultiTenantScheduler stopped")
        except Exception as e:
            logger.warning("Error stopping MultiTenantScheduler: %s", e)

    if hasattr(app.state, "manager_bot") and app.state.manager_bot:
        try:
            await app.state.manager_bot.session.close()
        except Exception as e:
            logger.warning("Error closing manager bot session: %s", e)

    if hasattr(app.state, "registry") and app.state.registry:
        await app.state.registry.close()

    if hasattr(app.state, "redis_client") and app.state.redis_client:
        await app.state.redis_client.aclose()

    await close_db()


def create_app(
    registry: Optional[BotRegistry] = None,
    dp: Optional[Dispatcher] = None,
    deduplicator: Optional[UpdateDeduplicator] = None,
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    redis_client: Optional[Redis] = None,
    manager_dp: Optional[Dispatcher] = None,
    manager_bot: Optional[Bot] = None,
    scheduler: Optional[MultiTenantScheduler] = None,
) -> FastAPI:
    """FastAPI application factory supporting dependency injection for testing."""
    app = FastAPI(
        title="Beauty Bot Webhook Engine",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Inject dependencies if provided (e.g. for testing)
    app.state.registry = registry
    app.state.dp = dp
    app.state.deduplicator = deduplicator
    app.state.session_factory = session_factory
    app.state.redis_client = redis_client
    app.state.manager_dp = manager_dp
    app.state.manager_bot = manager_bot
    app.state.scheduler = scheduler

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next: Any) -> Response:
        """Trace each incoming request with a unique correlation ID."""
        corr_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID")
        if not corr_id:
            corr_id = str(uuid.uuid4())
        request.state.correlation_id = corr_id

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = corr_id
        return response

    @app.get("/health/live", tags=["health"])
    async def health_live() -> dict[str, str]:
        """Lightweight liveness probe."""
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    async def health_ready() -> JSONResponse:
        """Readiness probe checking PostgreSQL and Redis connectivity without external calls."""
        db_status = "unknown"
        redis_status = "unknown"
        is_ready = True

        # Check DB
        try:
            sf = app.state.session_factory or async_session_factory
            async with sf() as session:
                await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2.0)
            db_status = "ok"
        except Exception as e:
            logger.error("Health ready DB check failed: %s", e)
            db_status = "error"
            is_ready = False

        # Check Redis
        try:
            rc = app.state.redis_client
            if rc is not None:
                await asyncio.wait_for(rc.ping(), timeout=2.0)
                redis_status = "ok"
            else:
                redis_status = "error"
                is_ready = False
        except Exception as e:
            logger.error("Health ready Redis check failed: %s", e)
            redis_status = "error"
            is_ready = False

        status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
        payload = {
            "status": "ready" if is_ready else "degraded",
            "database": db_status,
            "redis": redis_status,
        }
        return JSONResponse(status_code=status_code, content=payload)

    @app.post("/telegram/webhook/{public_bot_id}", tags=["webhook"])
    async def telegram_webhook(
        public_bot_id: str,
        request: Request,
        x_telegram_bot_api_secret_token: Optional[str] = Header(
            None, alias="X-Telegram-Bot-Api-Secret-Token"
        ),
    ) -> dict[str, Any]:
        """
        Receives Telegram webhook updates for the given public_bot_id.
        Enforces constant-time secret token verification, payload size limits,
        atomic deduplication, and feeds update to shared Dispatcher.
        """
        # 1. Parse public_bot_id UUID
        try:
            bot_uuid = uuid.UUID(public_bot_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")

        # 2. Check Content-Length and body size limit (413 Payload Too Large) via streaming reader
        raw_body = await read_limited_request_body(request, settings.webhook_max_body_bytes)

        # 3. Resolve BotInstance from Database
        session_maker = app.state.session_factory or async_session_factory
        async with session_maker() as session:
            repo = BotInstanceRepository(session)
            bot_instance = await repo.get_by_public_id(bot_uuid)

        if not bot_instance:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")

        # 4. Constant-time secret token verification
        expected_secret = bot_instance.webhook_secret or ""
        provided_secret = x_telegram_bot_api_secret_token or ""

        if not expected_secret or not secrets.compare_digest(provided_secret, expected_secret):
            logger.warning(
                "Webhook secret token mismatch or missing for bot instance #%s (%s)",
                bot_instance.id,
                public_bot_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Invalid secret token",
            )

        # 5. Check if BotInstance is in an inactive/forbidden status
        if not bot_instance.is_current or bot_instance.status in (
            BotInstanceStatus.DISABLED,
            BotInstanceStatus.ERROR,
            BotInstanceStatus.PROVISIONING,
        ):
            logger.warning(
                "Bot instance #%s has inactive status %s, rejecting update",
                bot_instance.id,
                bot_instance.status,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Bot instance is {bot_instance.status.value}"
                    if bot_instance.status != BotInstanceStatus.ACTIVE
                    else "Bot instance unavailable"
                ),
            )

        # 6. Parse and validate Telegram Update payload
        try:
            update_data = json.loads(raw_body.decode("utf-8"))
            update = Update.model_validate(update_data)
        except Exception as e:
            logger.warning(
                "Invalid Telegram update JSON for bot instance #%s: %s",
                bot_instance.id,
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid update payload",
            )

        # 7. Deduplication check in Redis
        deduplicator: UpdateDeduplicator = app.state.deduplicator
        try:
            acquired = await deduplicator.acquire(bot_instance.id, update.update_id)
        except DedupUnavailableError:
            raise HTTPException(status_code=503, detail="Update deduplication unavailable")
        if acquired.state == DedupState.COMPLETED:
            return {"ok": True, "status": "duplicate"}
        if acquired.state == DedupState.PROCESSING:
            # Telegram must retry if the first replica crashes mid-handler.
            raise HTTPException(status_code=503, detail="Update is still processing")
        claim = acquired.claim
        assert claim is not None

        try:
            # 8. Retrieve aiogram.Bot from BotRegistry
            registry: BotRegistry = app.state.registry
            async with session_maker() as session:
                bot = await registry.get_by_instance_id(
                    bot_instance.id,
                    session=session,
                    expected_token_version=bot_instance.token_version,
                )
            # 9. Feed update with a lease that renews for long-running handlers.
            dp: Dispatcher = app.state.dp
            async with deduplicator.maintain(claim):
                await dp.feed_update(
                    bot,
                    update,
                    bot_instance=bot_instance,
                    master_id=bot_instance.master_id,
                    webhook_update_scope=f"tenant:{bot_instance.id}",
                )
            await deduplicator.complete(claim)
            return {"ok": True}
        except (BotDisabledError, BotUnavailableError, BotProvisioningError) as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except BotNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
        except BotRegistryError as e:
            logger.error("Registry error for bot #%s: %s", bot_instance.id, e)
            raise HTTPException(status_code=500, detail="Bot registry error")
        except DedupUnavailableError:
            raise HTTPException(status_code=503, detail="Update deduplication unavailable")
        except Exception as e:
            logger.exception(
                "Error processing update %s for bot #%s: %s",
                update.update_id,
                bot_instance.id,
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal processing error",
            )
        finally:
            try:
                await deduplicator.release(claim)
            except DedupUnavailableError:
                logger.warning("Could not release deduplication claim for update %s", update.update_id)

    @app.post("/telegram/manager-webhook", tags=["manager-webhook"])
    async def telegram_manager_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: Optional[str] = Header(
            None, alias="X-Telegram-Bot-Api-Secret-Token"
        ),
    ) -> dict[str, Any]:
        """
        Receives Telegram webhook updates for the platform Manager Bot.
        Enforces constant-time secret token verification (settings.manager_webhook_secret),
        payload size limits, atomic deduplication, and feeds update to dedicated Manager Dispatcher.
        """
        # 1. Constant-time secret token verification
        expected_secret = settings.manager_webhook_secret or ""
        provided_secret = x_telegram_bot_api_secret_token or ""

        if not expected_secret or not secrets.compare_digest(provided_secret, expected_secret):
            logger.warning("Manager webhook secret token mismatch or missing")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Invalid secret token",
            )

        # 2. Check Content-Length and body size limit (413 Payload Too Large) via streaming reader
        raw_body = await read_limited_request_body(request, settings.webhook_max_body_bytes)

        # 3. Parse and validate Telegram Update payload
        try:
            update_data = json.loads(raw_body.decode("utf-8"))
            update = Update.model_validate(update_data)
        except Exception as e:
            logger.warning("Invalid Telegram update JSON for manager bot: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid update payload",
            )

        # 4. Check if manager_bot and manager_dp are configured
        manager_bot: Optional[Bot] = getattr(app.state, "manager_bot", None)
        manager_dp: Optional[Dispatcher] = getattr(app.state, "manager_dp", None)
        if not manager_bot or not manager_dp:
            logger.error("Manager bot or dispatcher is not configured")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Manager bot not configured",
            )

        # 5. Deduplication check in Redis
        deduplicator: UpdateDeduplicator = app.state.deduplicator
        try:
            acquired = await deduplicator.acquire_manager(update.update_id)
        except DedupUnavailableError:
            raise HTTPException(status_code=503, detail="Update deduplication unavailable")
        if acquired.state == DedupState.COMPLETED:
            return {"ok": True, "status": "duplicate"}
        if acquired.state == DedupState.PROCESSING:
            raise HTTPException(status_code=503, detail="Update is still processing")
        claim = acquired.claim
        assert claim is not None

        # 6. Feed update to dedicated Manager Dispatcher
        try:
            async with deduplicator.maintain(claim):
                await manager_dp.feed_update(
                    manager_bot,
                    update,
                    registry=getattr(app.state, "registry", None),
                    webhook_update_scope="manager",
                )
            await deduplicator.complete(claim)
            return {"ok": True}
        except DedupUnavailableError:
            raise HTTPException(status_code=503, detail="Update deduplication unavailable")
        except Exception as e:
            logger.exception("Error processing manager update %s: %s", update.update_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal processing error",
            )
        finally:
            try:
                await deduplicator.release(claim)
            except DedupUnavailableError:
                logger.warning("Could not release manager deduplication claim for update %s", update.update_id)

    return app
