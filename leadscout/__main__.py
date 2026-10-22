"""Запуск: python -m leadscout"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from .config import Settings
from .web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadscout", description="Личный ИИ-скаут фриланс-заказов")
    parser.add_argument("--demo", action="store_true", help="включить демо-режим с тестовыми заказами")
    parser.add_argument("--port", type=int, help="порт (по умолчанию 8000)")
    parser.add_argument("--host", help="адрес (по умолчанию 0.0.0.0)")
    args = parser.parse_args(argv)

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
