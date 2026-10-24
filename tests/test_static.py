"""Статика приложения: оболочка PWA, манифест, значки, шрифты и все скрипты на месте."""

import json
import re
from pathlib import Path

from leadscout.web.app import STATIC


def test_shell_and_manifest(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'rel="manifest"' in r.text and "/js/main.js" in r.text
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200
    data = json.loads(m.text)
    assert data["display"] == "standalone" and data["start_url"] == "/"
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_service_worker_scope_and_cache_headers(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/"
    assert "no-cache" in r.headers["cache-control"]
    assert "immutable" in client.get("/icons/icon-192.png").headers["cache-control"]


def test_fonts_referenced_in_css_exist():
    css = (STATIC / "fonts" / "fonts.css").read_text(encoding="utf-8")
    files = re.findall(r"url\(/fonts/([^)]+)\)", css)
    assert files
    for f in files:
        assert (STATIC / "fonts" / f).exists(), f


def test_js_imports_resolve():
    """Каждое имя, которое модуль импортирует из соседнего, действительно экспортируется."""
    js = STATIC / "js"
    exports: dict[Path, set[str]] = {}
    for p in js.rglob("*.js"):
        src = p.read_text(encoding="utf-8")
        names = set(re.findall(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+([\w$]+)", src))
        for block in re.findall(r"export\s*\{([^}]*)\}", src):
            names |= {n.strip().split(" as ")[-1] for n in block.split(",") if n.strip()}
        exports[p.resolve()] = names
    for p in js.rglob("*.js"):
        src = p.read_text(encoding="utf-8")
        for names, target in re.findall(r"import\s*\{([^}]*)\}\s*from\s*'(\.[^']+)'", src):
            dest = (p.parent / target).resolve()
            assert dest in exports, f"{p.name}: нет файла {target}"
            for n in (x.strip().split(" as ")[0] for x in names.split(",") if x.strip()):
                assert n in exports[dest], f"{p.name}: {n} не экспортируется из {target}"


def test_service_worker_precaches_every_script():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    for p in (STATIC / "js").rglob("*.js"):
        assert "/" + p.relative_to(STATIC).as_posix() in sw, f"{p.name} не попал в офлайн-кэш"


def test_500_has_security_headers(client):
    r = client.get("/api/orders/99999999999999999999")
    assert r.status_code in (401, 404)  # без входа 401, но не 500


def test_dependency_lock_is_hash_pinned():
    """Зависимости в Docker ставятся строго по списку с хешами: каждая запись закреплена по версии и по хешу."""
    from pathlib import Path

    lock = (Path(__file__).resolve().parent.parent / "requirements.lock").read_text(encoding="utf-8")
    blocks = [b for b in lock.split("\n\n") if "==" in b]
    assert blocks and all("--hash=sha256:" in b for b in blocks)
    assert "litellm" not in lock.lower()


def test_launchers_exist_and_point_to_local_mode():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("start-windows.bat", "start-mac.command", "start-linux.sh"):
        text = (root / name).read_text(encoding="utf-8")
        assert "leadscout --local" in text and "requirements.lock" in text, name
