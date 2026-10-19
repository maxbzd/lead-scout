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
