"""Настройки из переменных окружения (и файла .env, если он есть)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = 8000
    public_url: str = ""
    password: str = ""
    tz: str = "Europe/Moscow"
    demo: bool = False

    anthropic_api_key: str = ""
    ai_api_key: str = ""
    model_fast: str = "claude-haiku-5-5"
    model_smart: str = "claude-sonnet-5-5"

    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session: str = ""
    bot_token: str = ""
    owner_id: int = 0

    scan_minutes: int = 10
    usd_rub: float = 95.0
    eur_rub: float = 105.0

    run_worker: bool = True
    cookie_secure: bool | None = None
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "leadscout.db"

    @classmethod
    def load(cls, env_file: str | None = None) -> Settings:
        _load_dotenv(Path(env_file or ".env"))
        data_dir = Path(os.environ.get("LS_DATA_DIR", "data"))
        s = cls(
            data_dir=data_dir,
            host=os.environ.get("LS_HOST", "0.0.0.0"),
            port=_int("LS_PORT", 8000),
            public_url=os.environ.get("LS_PUBLIC_URL", "").rstrip("/"),
            password=os.environ.get("LS_PASSWORD", ""),
            tz=os.environ.get("LS_TZ", "Europe/Moscow"),
            demo=_bool("LS_DEMO", False),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            ai_api_key=os.environ.get("LS_AI_API_KEY", "").strip(),
            model_fast=os.environ.get("LS_MODEL_FAST", "claude-haiku-5-5"),
            model_smart=os.environ.get("LS_MODEL_SMART", "claude-sonnet-5-5"),
            telegram_api_id=_int("TELEGRAM_API_ID", 0),
            telegram_api_hash=os.environ.get("TELEGRAM_API_HASH", "").strip(),
            telegram_session=os.environ.get("TELEGRAM_SESSION", "").strip(),
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            owner_id=_int("TELEGRAM_OWNER_ID", 0),
            scan_minutes=max(2, _int("LS_SCAN_MINUTES", 10)),
            usd_rub=float(os.environ.get("LS_USD_RUB", "95") or 95),
            eur_rub=float(os.environ.get("LS_EUR_RUB", "105") or 105),
            run_worker=_bool("LS_WORKER", True),
        )
        secure = os.environ.get("LS_COOKIE_SECURE")
        if secure not in (None, ""):
            s.cookie_secure = _bool("LS_COOKIE_SECURE")
        return s

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolve_password(self) -> tuple[str, bool]:
        """Пароль для входа. Если не задан, создаём случайный и храним в data/password.txt."""
        if self.password:
            return self.password, False
        self.ensure_dirs()
        path = self.data_dir / "password.txt"
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value, False
        value = secrets.token_urlsafe(9)
        path.write_text(value + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value, True
