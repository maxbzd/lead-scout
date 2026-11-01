"""Установленная программа для компьютера: значок в меню, своё окно, без чёрной консоли.

Запускает ту же локальную версию (--local), но открывает окно приложения вместо вкладки браузера.
Если программа уже работает (например, держит связь с облачным сайтом), значок просто открывает её окно.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

CANDIDATES_WIN = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
)
CANDIDATES_NIX = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")


def find_browser() -> str | None:
    if sys.platform == "win32":
        for raw in CANDIDATES_WIN:
            path = os.path.expandvars(raw)
            if "%" not in path and os.path.isfile(path):
                return path
        return None
    for name in CANDIDATES_NIX:
        found = shutil.which(name) or (name if os.path.isfile(name) else None)
        if found:
            return found
    return None


def open_window(url: str) -> None:
    """Окно без адресной строки, как у обычного приложения. Нет Edge или Chrome: обычная вкладка браузера."""
    browser = find_browser()
    if browser:
        try:
            subprocess.Popen([browser, f"--app={url}", "--window-size=1280,860"], close_fds=True)
            return
        except OSError:
            pass
    webbrowser.open(url)


def port_file(data_dir: Path) -> Path:
    return data_dir / "app.port"


def alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/session", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("authed") is True
    except (OSError, ValueError):
        return False


DEFAULT_PORT = 8731  # редкий порт: не мешает другим программам, а адрес окна не меняется между запусками
MAX_LOG = 3 * 1024 * 1024


def _rotate(path) -> None:
    try:
        if path.stat().st_size > MAX_LOG:
            path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass


def _fatal(message: str) -> None:
    """Программа без консоли не может написать об ошибке в окне терминала: показываем окно Windows."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Scout", 0x10)  # type: ignore[attr-defined]
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except Exception:
        logging.getLogger("leadscout").exception("Scout не запустился")
        _fatal("Scout не запустился.\n\nПодробности записаны в файл scout.log в папке .leadscout вашей домашней папки. Пришлите его, и я разберусь.")
        return 1


def _main(argv: list[str] | None = None) -> int:
    background = "--background" in (sys.argv[1:] if argv is None else argv)  # запуск вместе с Windows: окно не открываем
    os.environ["LS_LOCAL"] = "1"
    os.environ.setdefault("LS_PORT", str(DEFAULT_PORT))
    from .config import Settings

    settings = Settings.load()
    settings.ensure_dirs()
    pf = port_file(settings.data_dir)

    # без консоли у программы нет stdout: пишем журнал в файл, иначе любой print уронил бы её
    log_path = settings.data_dir / "scout.log"
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        _rotate(log_path)
        stream = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = stream
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr, force=True)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        running = int(pf.read_text().strip())
    except (OSError, ValueError):
        running = 0
    if running and alive(running):
        if not background:
            open_window(f"http://localhost:{running}")  # программа уже работает: показываем её окно
        return 0

    from .__main__ import free_port, run_local

    settings.port = free_port(settings.host, settings.port)
    try:
        return run_local(settings, opener=(lambda url: None) if background else open_window,
                         on_ready=lambda port: pf.write_text(str(port)), announce=False)
    finally:
        try:
            pf.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
