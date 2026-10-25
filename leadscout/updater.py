"""Обновление установленной программы одной кнопкой: берёт новую версию из выпусков GitHub и запускает установщик."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from ._build import BUILD
from .util import iso

log = logging.getLogger("leadscout.updater")

REPO = os.environ.get("LS_UPDATE_REPO", "maxbzd/lead-scout")
ASSET = "Scout-Setup.exe"
TAG = re.compile(r"^app-(\d+)$")
CHECK_EVERY = 6 * 3600
MAX_SIZE = 400 * 1024 * 1024
VERSION = f"1.0.{BUILD}"


class UpdateError(Exception):
    """Понятная ошибка обновления."""


def can_install() -> bool:
    """Ставить обновление кнопкой можно только в установленной программе для Windows."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


class Updater:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.latest: int | None = None
        self.url: str | None = None
        self.digest: str | None = None
        self.size = 0
        self.checked_at: str | None = None
        self.error: str | None = None
        self._mono = 0.0

    def status(self) -> dict:
        newer = self.latest is not None and self.latest > BUILD
        return {"current": VERSION, "latest": f"1.0.{self.latest}" if self.latest is not None else None, "available": newer,
                "can_install": can_install() and newer, "installable_here": can_install(), "checked_at": self.checked_at, "error": self.error}

    def due(self, now_mono: float) -> bool:
        return not self._mono or now_mono - self._mono > CHECK_EVERY

    async def check(self, now_mono: float = 0.0) -> dict:
        self._mono = now_mono or 1.0
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=True) as c:
                r = await c.get(f"https://api.github.com/repos/{REPO}/releases/latest", headers={"Accept": "application/vnd.github+json"})
        except httpx.HTTPError:
            self.error = "Не удалось проверить обновления: нет связи с GitHub"
            return self.status()
        self.checked_at = iso()
        if r.status_code == 404:
            self.error = "Новых версий не нашёл (выпусков пока нет или репозиторий закрыт)"
            return self.status()
        if r.status_code >= 400:
            self.error = f"GitHub ответил ошибкой {r.status_code}"
            return self.status()
        try:
            data = r.json()
            m = TAG.match(str(data.get("tag_name", "")))
            asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
        except (ValueError, AttributeError):
            self.error = "GitHub вернул непонятный ответ"
            return self.status()
        if not m or not asset:
            self.error = "В последнем выпуске нет установщика"
            return self.status()
        self.latest = int(m.group(1))
        self.url = asset.get("browser_download_url")
        self.digest = str(asset.get("digest") or "").removeprefix("sha256:").lower() or None
        self.size = int(asset.get("size") or 0)
        self.error = None
        return self.status()

    async def download(self, folder: Path | None = None) -> Path:
        if not self.url or self.latest is None or self.latest <= BUILD:
            raise UpdateError("Обновлений нет")
        if not self.url.startswith("https://"):
            raise UpdateError("Адрес обновления выглядит подозрительно")
        folder = folder or Path(tempfile.mkdtemp(prefix="scout-update-"))
        path = folder / ASSET
        h = hashlib.sha256()
        total = 0
        try:
            async with httpx.AsyncClient(timeout=600, transport=self.transport, follow_redirects=True) as c, c.stream("GET", self.url) as r:
                if r.status_code >= 400:
                    raise UpdateError(f"Не удалось скачать обновление (ошибка {r.status_code}). Если репозиторий закрытый, скачайте установщик вручную")
                with path.open("wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        total += len(chunk)
                        if total > MAX_SIZE:
                            raise UpdateError("Файл обновления слишком большой")
                        h.update(chunk)
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise UpdateError("Обрыв связи при скачивании обновления") from e
        if self.size and total != self.size:
            raise UpdateError("Обновление скачалось не целиком, попробуйте ещё раз")
        if self.digest and h.hexdigest() != self.digest:
            path.unlink(missing_ok=True)
            raise UpdateError("Контрольная сумма обновления не сошлась: файл не установлен")
        return path

    @staticmethod
    def launch(installer: Path) -> None:
        """Запускаем установщик отдельно от программы: она закроется, он заменит файлы и откроет новую версию."""
        if not can_install():
            raise UpdateError("Автообновление работает только в установленной программе для Windows")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=flags, close_fds=True)
