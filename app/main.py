import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.models import SOURCES, ApplicationUpdate, RecruiterBan, SearchInput, Source
from app.monitor import Monitor
from app.store import Store
from app.telegram import DeliveryError

STATIC = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


def _normalize_origin(value: str) -> str | None:
    """Return a canonical web origin, rejecting values that are not origins."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            return None
        host = parsed.hostname.lower()
        port = parsed.port
        if port and not (parsed.scheme.lower() == "http" and port == 80) and not (
            parsed.scheme.lower() == "https" and port == 443
        ):
            host = f"{host}:{port}"
        return f"{parsed.scheme.lower()}://{host}"
    except (ValueError, TypeError):
        return None


def create_app(settings: Settings | None = None, run_monitor=True):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(settings.database)
        app.state.monitor = Monitor(app.state.store, settings)
        if run_monitor:
            await app.state.monitor.start()
        try:
            yield
        finally:
            if run_monitor:
                await app.state.monitor.stop()
            app.state.store.close()

    app = FastAPI(title="Job Watcher", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if settings.access_token:
                supplied = request.headers.get("authorization", "")
                if not secrets.compare_digest(supplied, "Bearer " + settings.access_token):
                    return JSONResponse({"detail": "Access token required"}, status_code=401)
            else:
                host = request.client.host if request.client else ""
                if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
                    return JSONResponse(
                        {"detail": "Set APP_ACCESS_TOKEN for remote access"}, status_code=403
                    )
            # Reject browser writes from another origin, including form-based localhost attacks.
            origin = request.headers.get("origin")
            if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
                request_origin = _normalize_origin(str(request.base_url))
                allowed_origins = {request_origin} if request_origin else set()
                configured_origin = _normalize_origin(settings.public_origin)
                if configured_origin:
                    allowed_origins.add(configured_origin)
                if _normalize_origin(origin) not in allowed_origins:
                    logger.warning(
                        "Rejected cross-origin API write: origin=%r request_origin=%r",
                        origin,
                        request_origin,
                    )
                    return JSONResponse(
                        {"detail": "Cross-origin writes are not allowed"}, status_code=403
                    )
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status(request: Request):
        store, monitor = request.app.state.store, request.app.state.monitor
        searches = store.searches()
        return {
            "server_time": time.time(),
            "monitor_running": monitor.running,
            "telegram_configured": settings.telegram_configured,
            "telegram_error": monitor.delivery_error,
            "source_gap_seconds": settings.source_gap,
            "summary": store.summary(),
            "searches": searches,
            "sources": [
                {"source": source, "next_request": store.source_ready_at(source)}
                for source in SOURCES
            ],
        }

    @app.get("/api/jobs")
    async def jobs(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        source: Source | None = None,
        status: Literal["baseline", "pending", "sent", "failed"] | None = None,
        q: str | None = Query(default=None, max_length=200),
    ):
        return request.app.state.store.jobs(limit, source, status, q)

    @app.get("/api/recruiter-bans")
    async def recruiter_bans(request: Request):
        return request.app.state.store.banned_recruiters()

    @app.post("/api/recruiter-bans")
    async def ban_recruiter(ban: RecruiterBan, request: Request):
        return request.app.state.store.ban_recruiter(ban.name)

    @app.delete("/api/recruiter-bans/{name}")
    async def unban_recruiter(name: str, request: Request):
        if not request.app.state.store.unban_recruiter(name):
            raise HTTPException(404, "Recruiter not found")
        return {"ok": True}

    @app.patch("/api/jobs/{job_key}/application")
    async def update_application(job_key: str, update: ApplicationUpdate, request: Request):
        if not request.app.state.store.set_applied(job_key, update.applied):
            raise HTTPException(404, "Opportunity not found")
        return {"key": job_key, "applied": update.applied}

    @app.post("/api/searches", status_code=201)
    async def add_search(config: SearchInput, request: Request):
        store = request.app.state.store
        if len(store.searches()) >= 30:
            raise HTTPException(400, "Maximum 30 saved searches")
        return store.add_search(config)

    @app.put("/api/searches/{search_id}")
    async def update_search(search_id: int, config: SearchInput, request: Request):
        result = request.app.state.store.update_search(search_id, config)
        if not result:
            raise HTTPException(404, "Search not found")
        return result

    @app.delete("/api/searches/{search_id}")
    async def delete_search(search_id: int, request: Request):
        if not request.app.state.store.delete_search(search_id):
            raise HTTPException(404, "Search not found")
        return {"ok": True}

    @app.post("/api/searches/{search_id}/check", status_code=202)
    async def check_search(search_id: int, request: Request):
        search = request.app.state.store.search(search_id)
        if not search:
            raise HTTPException(404, "Search not found")
        if not search["enabled"]:
            raise HTTPException(400, "Resume the search before checking")
        request.app.state.store.request_check(search_id)
        return {"ok": True, "detail": "Check queued; source cooldowns still apply"}

    @app.post("/api/telegram/test")
    async def telegram_test(request: Request):
        monitor = request.app.state.monitor
        if not settings.telegram_configured:
            raise HTTPException(
                400, "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env, then restart"
            )
        if not monitor.telegram:
            raise HTTPException(503, "Notification worker is not running")
        if time.time() < monitor.telegram_gate:
            raise HTTPException(429, "Telegram is cooling down; try again shortly")
        monitor.telegram_gate = time.time() + 2
        try:
            await monitor.telegram.send(
                "<b>Job Watcher is connected.</b>\nNew job alerts will arrive here."
            )
        except DeliveryError as exc:
            raise HTTPException(502, str(exc)) from None
        return {"ok": True}

    @app.post("/api/telegram/retry")
    async def retry_telegram(request: Request):
        request.app.state.store.retry_failed()
        request.app.state.monitor.wake_delivery.set()
        return {"ok": True}

    return app


app = create_app()
