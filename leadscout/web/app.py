"""Сборка веб-приложения: API, статические файлы приложения (PWA), защитные заголовки."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings
from ..runtime import Runtime
from .api import HUMAN_ERRORS, public, router
from .auth import Auth

log = logging.getLogger("leadscout.web")
STATIC = Path(__file__).parent / "static"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def create_app(settings: Settings, runtime: Runtime | None = None) -> FastAPI:
    rt = runtime or Runtime(settings)
    password, _ = settings.resolve_password()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await rt.start()
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(title="Lead Scout", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.rt = rt
    app.state.settings = settings
    app.state.last_request = time.monotonic()
    app.state.auth = Auth(password, rt.secrets.signing_key(), rt.db)
    rt.app = app  # облачной синхронизации нужен вход в собственный API программы

    app.include_router(public)
    app.include_router(router)

    async def human(request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    for kind in HUMAN_ERRORS:  # ошибки с понятным текстом показываем как есть
        app.add_exception_handler(kind, human)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        # этот обработчик работает вне общего слоя заголовков, поэтому защитные заголовки задаём здесь
        return JSONResponse({"detail": "Что-то пошло не так на сервере. Попробуйте ещё раз"}, status_code=500,
                            headers={"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.middleware("http")
    async def headers(request: Request, call_next):
        if request.headers.get("x-scout-internal") != "1":
            app.state.last_request = time.monotonic()  # служебные запросы синхронизации не считаются «окно ещё открыто»
        if settings.local:
            # приложение для одного компьютера: чужие сайты не должны достучаться до него через подмену адреса (DNS rebinding)
            host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in {"localhost", "127.0.0.1", "::1"}:
                return JSONResponse({"detail": "Доступ только с этого компьютера"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        path = request.url.path
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif path.endswith((".woff2", ".png")) and path.startswith(("/fonts/", "/icons/")):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/"})

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
