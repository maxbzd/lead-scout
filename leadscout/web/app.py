"""Сборка веб-приложения: API, статические файлы приложения (PWA), защитные заголовки."""

from __future__ import annotations

import logging
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
    app.state.auth = Auth(password, rt.secrets.signing_key(), rt.db)

    app.include_router(public)
    app.include_router(router)

    async def human(request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    for kind in HUMAN_ERRORS:  # ошибки с понятным текстом показываем как есть
        app.add_exception_handler(kind, human)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "Что-то пошло не так на сервере. Попробуйте ещё раз"}, status_code=500)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        path = request.url.path
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif path.startswith(("/fonts/", "/icons/")):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/"})

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
