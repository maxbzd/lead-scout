"""Запуск: python -m leadscout"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from .config import Settings
from .web.app import create_app

log = logging.getLogger("leadscout")


def free_port(host: str, start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as sock:
            if sock.connect_ex((host, port)) != 0:  # порт свободен
                return port
    return start


def should_quit(now: float, started: float, last_request: float, clients: int, busy: bool,
                grace: float = 120.0, idle: float = 90.0) -> bool:
    """Окно закрыли: ни открытых соединений, ни запросов, ни работы в фоне. Тогда программа останавливается сама."""
    if now - started < grace or clients > 0 or busy:
        return False
    return now - last_request > idle


def run_local(settings: Settings, opener=None, on_ready=None, announce: bool = True) -> int:
    """opener открывает окно (в установленной программе это своё окно без адресной строки), on_ready сообщает порт."""
    app = create_app(settings)
    url = f"http://localhost:{settings.port}"
    config = uvicorn.Config(app, host=settings.host, port=settings.port, timeout_graceful_shutdown=3, log_level="warning")
    server = uvicorn.Server(config)
    started = time.monotonic()
    app.state.quit = lambda: setattr(server, "should_exit", True)  # кнопка «Закрыть программу»

    async def watchdog() -> None:
        while not server.should_exit:
            await asyncio.sleep(float(os.environ.get("LS_QUIT_CHECK", 10)))
            rt = app.state.rt
            busy = rt.scanning or bool(rt._tasks) or rt.cloud.enabled or rt.scan_minutes > 0  # облако и проверка по расписанию требуют работающей программы
            if should_quit(time.monotonic(), started, app.state.last_request, rt.sse_clients, busy,
                           grace=float(os.environ.get("LS_QUIT_GRACE", 120)), idle=float(os.environ.get("LS_QUIT_IDLE", 90))):
                log.info("Окно закрыто, останавливаюсь")
                server.should_exit = True

    async def serve() -> None:
        watcher = asyncio.create_task(watchdog())
        try:
            await server.serve()
        finally:
            watcher.cancel()

    if announce:
        print(f"\n  Scout запущен: {url}\n  Данные хранятся в {settings.data_dir}\n"
              "  Закройте окно приложения, и программа остановится сама (или нажмите Ctrl+C).\n  Если подключён облачный сайт, программа работает, пока открыто это чёрное окно.\n", flush=True)
    if on_ready:
        on_ready(settings.port)
    threading.Timer(1.5, opener or webbrowser.open, [url]).start()
    asyncio.run(serve())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadscout", description="Личный ИИ-скаут фриланс-заказов")
    parser.add_argument("--demo", action="store_true", help="включить демо-режим с тестовыми заказами")
    parser.add_argument("--port", type=int, help="порт (по умолчанию 8000)")
    parser.add_argument("--host", help="адрес (по умолчанию 0.0.0.0)")
    parser.add_argument("--local", action="store_true",
                        help="приложение на этом компьютере: без пароля, только с этого компьютера, проверка чатов по кнопке, закрытие вместе с окном")
    args = parser.parse_args(argv)
    if args.local:
        os.environ["LS_LOCAL"] = "1"

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):  # в адресах запросов к Telegram есть токен бота
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = Settings.load()
    if args.demo:
        settings.demo = True
    if args.port:
        settings.port = args.port
    if args.host:
        settings.host = args.host

    if settings.local:
        settings.port = args.port or free_port(settings.host, settings.port)
        return run_local(settings)

    password, generated = settings.resolve_password()
    if generated:
        bar = "=" * 60
        print(f"\n{bar}\n  ПАРОЛЬ ДЛЯ ВХОДА В ПРИЛОЖЕНИЕ: {password}\n"
              f"  Он сохранён в {settings.data_dir / 'password.txt'}.\n"
              f"  Чтобы задать свой, укажите LS_PASSWORD в файле .env\n{bar}\n", flush=True)

    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, proxy_headers=True, timeout_graceful_shutdown=3, forwarded_allow_ips=os.environ.get("LS_TRUSTED_PROXIES", "127.0.0.1"), log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
