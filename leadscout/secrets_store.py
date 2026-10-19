"""Секреты (ключи, токены, сессия Telegram): храним в базе в зашифрованном виде.

Ключ шифрования лежит рядом с базой в файле secret.key и создаётся при первом запуске.
Значения из переменных окружения важнее сохранённых в приложении.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets as pysecrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings
from .db import Database

ENV_MAP = {
    "anthropic_key": "anthropic_api_key",
    "ai_key": "ai_api_key",
    "tg_api_id": "telegram_api_id",
    "tg_api_hash": "telegram_api_hash",
    "tg_session": "telegram_session",
    "bot_token": "bot_token",
    "owner_id": "owner_id",
}


def load_or_create_key(path: Path) -> bytes:
    if path.is_file():
        raw = path.read_bytes().strip()
        if raw:
            return raw
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.urlsafe_b64encode(pysecrets.token_bytes(32))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return raw


class Secrets:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        settings.ensure_dirs()
        self._key = load_or_create_key(settings.data_dir / "secret.key")
        self._fernet = Fernet(self._key)

    # подпись cookie и прочее: отдельный ключ, выведенный из главного
    def signing_key(self) -> bytes:
        return hashlib.sha256(b"cookie:" + self._key).digest()

    def _env(self, name: str):
        attr = ENV_MAP.get(name)
        val = getattr(self.settings, attr, "") if attr else ""
        return val if val not in ("", 0, None) else None

    def get(self, name: str) -> str | None:
        env = self._env(name)
        if env is not None:
            return str(env)
        raw = self.db.scalar("SELECT value FROM settings WHERE key=?", (f"secret:{name}",))
        if raw is None:
            return None
        try:
            import json

            token = json.loads(raw)
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return None

    def source(self, name: str) -> str:
        """Откуда значение: env (задано на сервере), app (введено в приложении) или none."""
        if self._env(name) is not None:
            return "env"
        return "app" if self.db.scalar("SELECT 1 FROM settings WHERE key=?", (f"secret:{name}",)) else "none"

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def set(self, name: str, value: str) -> None:
        self.db.set_setting(f"secret:{name}", self._fernet.encrypt(value.encode("utf-8")).decode("ascii"))

    def delete(self, name: str) -> None:
        self.db.del_setting(f"secret:{name}")


def mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "•" * len(value)
    return value[:keep] + "…" + value[-keep:]
