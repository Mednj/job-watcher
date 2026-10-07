import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.models import (
    SOURCES,
    ApplicationUpdate,
    LoginInput,
    PasswordChange,
    RecruiterBan,
    SearchInput,
    Source,
    TelegramSettings,
    UserCreate,
)
from app.monitor import Monitor
from app.store import Store
from app.telegram import DeliveryError, Telegram

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
        if (
            port
            and not (parsed.scheme.lower() == "http" and port == 80)
            and not (parsed.scheme.lower() == "https" and port == 443)
        ):
            host = f"{host}:{port}"
        return f"{parsed.scheme.lower()}://{host}"
    except (ValueError, TypeError):
        return None


def create_app(settings: Settings | None = None, run_monitor=True):
    settings = settings or Settings.from_env()
    auth_attempts = {}

    def check_auth_rate_limit(request, scope, maximum, window_seconds):
        key = (scope, request.client.host if request.client else "unknown")
        now = time.time()
        recent = [stamp for stamp in auth_attempts.get(key, []) if now - stamp < window_seconds]
        if len(recent) >= maximum:
            auth_attempts[key] = recent
            return False
        recent.append(now)
        auth_attempts[key] = recent
        return True

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(
            settings.database,
            settings.access_token,
            settings.telegram_token,
            settings.telegram_chat_id,
        )
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
            if request.url.path not in ("/api/auth/login", "/api/auth/register"):
                supplied = request.headers.get("authorization", "")
                token = supplied[7:] if supplied.startswith("Bearer ") else ""
                user = request.app.state.store.session_user(token)
                host = request.client.host if request.client else ""
                if user:
                    request.state.user = user
                elif not settings.access_token:
                    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
                        return JSONResponse(
                            {"detail": "Set APP_ACCESS_TOKEN before enabling remote access"},
                            status_code=403,
                        )
                    return JSONResponse({"detail": "Sign in to continue"}, status_code=401)
                else:
                    return JSONResponse({"detail": "Sign in to continue"}, status_code=401)
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
        user_id = request.state.user["id"]
        searches = store.searches(user_id)
        for search in searches:
            search.pop("user_id", None)
        return {
            "user": {
                "id": request.state.user["id"],
                "username": request.state.user["username"],
                "role": request.state.user["role"],
            },
            "server_time": time.time(),
            "monitor_running": monitor.running,
            "telegram_configured": store.telegram_settings(user_id)["configured"],
            "telegram_error": monitor.delivery_error.get(user_id),
            "source_gap_seconds": settings.source_gap,
            "summary": store.summary(user_id),
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
        jobs = request.app.state.store.jobs(limit, source, status, q, request.state.user["id"])
        for job in jobs:
            job.pop("user_id", None)
        return jobs

    @app.post("/api/auth/login")
    async def login(credentials: LoginInput, request: Request):
        if not check_auth_rate_limit(request, "login", 10, 900):
            raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.")
        user = request.app.state.store.authenticate(credentials.username, credentials.password)
        if not user:
            raise HTTPException(401, "Username or password is incorrect")
        return {
            "token": request.app.state.store.create_session(user["id"]),
            "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
        }

    @app.post("/api/auth/register", status_code=201)
    async def register(credentials: UserCreate, request: Request):
        if not check_auth_rate_limit(request, "register", 5, 3600):
            raise HTTPException(429, "Too many accounts created from this connection. Try later.")
        try:
            user = request.app.state.store.add_user(credentials.username, credentials.password)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(409, "That username is already in use") from None
            raise
        return {
            "token": request.app.state.store.create_session(user["id"]),
            "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
        }

    @app.get("/api/auth/me")
    async def current_user(request: Request):
        user = request.state.user
        return {"id": user["id"], "username": user["username"], "role": user["role"]}

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.startswith("Bearer ") else ""
        request.app.state.store.revoke_session(token)
        return {"ok": True}

    @app.put("/api/auth/password")
    async def change_password(change: PasswordChange, request: Request):
        if not request.app.state.store.change_password(
            request.state.user["id"], change.current_password, change.new_password
        ):
            raise HTTPException(400, "Current password is incorrect")
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.startswith("Bearer ") else ""
        request.app.state.store.revoke_session(token)
        return {"ok": True, "detail": "Password changed; sign in again"}

    @app.get("/api/users")
    async def users(request: Request):
        if request.state.user["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        return request.app.state.store.users()

    @app.post("/api/users", status_code=201)
    async def add_user(user: UserCreate, request: Request):
        if request.state.user["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        try:
            created = request.app.state.store.add_user(user.username, user.password)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(409, "That username is already in use") from None
            raise
        return {"id": created["id"], "username": created["username"], "role": created["role"]}

    @app.patch("/api/users/{user_id}/active")
    async def set_user_active(user_id: int, active: bool, request: Request):
        if request.state.user["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        if user_id == request.state.user["id"] and not active:
            raise HTTPException(400, "You cannot disable your own account")
        if not request.app.state.store.set_user_active(user_id, active):
            target = request.app.state.store.user(user_id)
            if not target:
                raise HTTPException(404, "User not found")
            raise HTTPException(400, "Keep at least one administrator active")
        return {"ok": True}

    @app.get("/api/telegram/settings")
    async def telegram_settings(request: Request):
        return request.app.state.store.telegram_settings(request.state.user["id"])

    @app.put("/api/telegram/settings")
    async def save_telegram_settings(config: TelegramSettings, request: Request):
        request.app.state.store.save_telegram_settings(
            request.state.user["id"], config.bot_token, config.chat_id
        )
        return request.app.state.store.telegram_settings(request.state.user["id"])

    @app.delete("/api/telegram/settings")
    async def clear_telegram_settings(request: Request):
        request.app.state.store.clear_telegram_settings(request.state.user["id"])
        return {"configured": False, "chat_id": ""}

    @app.get("/api/recruiter-bans")
    async def recruiter_bans(request: Request):
        return request.app.state.store.banned_recruiters(request.state.user["id"])

    @app.post("/api/recruiter-bans")
    async def ban_recruiter(ban: RecruiterBan, request: Request):
        return request.app.state.store.ban_recruiter(ban.name, request.state.user["id"])

    @app.delete("/api/recruiter-bans/{name}")
    async def unban_recruiter(name: str, request: Request):
        if not request.app.state.store.unban_recruiter(name, request.state.user["id"]):
            raise HTTPException(404, "Recruiter not found")
        return {"ok": True}

    @app.patch("/api/jobs/{job_key}/application")
    async def update_application(job_key: str, update: ApplicationUpdate, request: Request):
        if not request.app.state.store.set_applied(
            job_key, update.applied, request.state.user["id"]
        ):
            raise HTTPException(404, "Opportunity not found")
        return {"key": job_key, "applied": update.applied}

    @app.post("/api/searches", status_code=201)
    async def add_search(config: SearchInput, request: Request):
        store = request.app.state.store
        user_id = request.state.user["id"]
        if len(store.searches(user_id)) >= 30:
            raise HTTPException(400, "Maximum 30 saved searches")
        return store.add_search(config, user_id)

    @app.put("/api/searches/{search_id}")
    async def update_search(search_id: int, config: SearchInput, request: Request):
        result = request.app.state.store.update_search(search_id, config, request.state.user["id"])
        if not result:
            raise HTTPException(404, "Search not found")
        return result

    @app.delete("/api/searches/{search_id}")
    async def delete_search(search_id: int, request: Request):
        if not request.app.state.store.delete_search(search_id, request.state.user["id"]):
            raise HTTPException(404, "Search not found")
        return {"ok": True}

    @app.post("/api/searches/{search_id}/check", status_code=202)
    async def check_search(search_id: int, request: Request):
        search = request.app.state.store.search(search_id, request.state.user["id"])
        if not search:
            raise HTTPException(404, "Search not found")
        if not search["enabled"]:
            raise HTTPException(400, "Resume the search before checking")
        request.app.state.store.request_check(search_id, request.state.user["id"])
        return {"ok": True, "detail": "Check queued; source cooldowns still apply"}

    @app.post("/api/telegram/test")
    async def telegram_test(request: Request):
        monitor = request.app.state.monitor
        user = request.app.state.store.user(request.state.user["id"])
        if not (user["telegram_token"] and user["telegram_chat_id"]):
            raise HTTPException(
                400, "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env, then restart"
            )
        if not monitor.telegram_client:
            raise HTTPException(503, "Notification worker is not running")
        user_id = request.state.user["id"]
        if time.time() < monitor.telegram_gate.get(user_id, 0):
            raise HTTPException(429, "Telegram is cooling down; try again shortly")
        monitor.telegram_gate[user_id] = time.time() + 2
        try:
            personal_bot = Telegram(
                SimpleNamespace(
                    telegram_token=user["telegram_token"],
                    telegram_chat_id=user["telegram_chat_id"],
                    telegram_configured=True,
                ),
                monitor.telegram_client,
            )
            await personal_bot.send(
                "<b>Job Watcher is connected.</b>\nNew job alerts will arrive here."
            )
        except DeliveryError as exc:
            raise HTTPException(502, str(exc)) from None
        return {"ok": True}

    @app.post("/api/telegram/retry")
    async def retry_telegram(request: Request):
        request.app.state.store.retry_failed(request.state.user["id"])
        request.app.state.monitor.wake_delivery.set()
        return {"ok": True}

    return app


app = create_app()
