"""Обновление одной кнопкой: новая версия из выпусков GitHub, проверка файла, запуск установщика."""

import asyncio
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from leadscout import updater
from leadscout.config import Settings
from leadscout.updater import UpdateError, Updater
from leadscout.web.app import create_app

from .conftest import H

BODY = b"fake installer bytes" * 1000
SHA = hashlib.sha256(BODY).hexdigest()


def release(tag="app-7", digest=f"sha256:{SHA}", size=len(BODY), name="Scout-Setup.exe"):
    return {"tag_name": tag, "assets": [{"name": name, "browser_download_url": "https://github.com/x/releases/download/Scout-Setup.exe",
                                          "digest": digest, "size": size}]}


def make(handler):
    return Updater(transport=httpx.MockTransport(handler))


def api_ok(rel):
    def handler(req):
        if "api.github.com" in req.url.host:
            return httpx.Response(200, json=rel)
        return httpx.Response(200, content=BODY)
    return handler


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def build_3(monkeypatch):
    monkeypatch.setattr(updater, "BUILD", 3)


def test_newer_release_is_offered():
    u = make(api_ok(release("app-7")))
    s = run(u.check())
    assert s["available"] and s["latest"] == "1.0.7" and s["error"] is None


def test_same_or_older_release_is_not_offered():
    for tag in ("app-3", "app-2"):
        assert not run(make(api_ok(release(tag))).check())["available"]


def test_odd_releases_do_not_break_anything():
    assert "нет установщика" in run(make(api_ok(release(name="other.zip"))).check())["error"]
    assert "нет установщика" in run(make(api_ok(release("v9"))).check())["error"]
    u = make(lambda r: httpx.Response(404, json={}))
    assert not run(u.check())["available"] and run(u.check())["error"]
    u = make(lambda r: (_ for _ in ()).throw(httpx.ConnectError("нет сети")))
    assert "нет связи" in run(u.check())["error"]


def test_download_checks_size_and_checksum(tmp_path):
    u = make(api_ok(release()))
    run(u.check())
    path = run(u.download(tmp_path))
    assert path.read_bytes() == BODY
    bad = make(api_ok(release(digest="sha256:" + "0" * 64)))
    run(bad.check())
    with pytest.raises(UpdateError, match="Контрольная сумма"):
        run(bad.download(tmp_path))
    assert not (tmp_path / updater.ASSET).exists()
    short = make(api_ok(release(size=len(BODY) + 5)))
    run(short.check())
    with pytest.raises(UpdateError, match="не целиком"):
        run(short.download(tmp_path))


def test_download_refuses_when_there_is_nothing_new(tmp_path):
    u = make(api_ok(release("app-3")))
    run(u.check())
    with pytest.raises(UpdateError):
        run(u.download(tmp_path))


def test_launch_only_in_the_installed_windows_app(monkeypatch, tmp_path):
    with pytest.raises(UpdateError):
        Updater.launch(tmp_path / "x.exe")
    calls = []
    monkeypatch.setattr(updater, "can_install", lambda: True)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    Updater.launch(tmp_path / "Scout-Setup.exe")
    assert calls[0][1:] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]


def local(tmp_path):
    s = Settings(data_dir=tmp_path, local=True, demo=True, run_worker=False, host="127.0.0.1")
    return TestClient(create_app(s), base_url="http://localhost:8000")


def test_api_reports_and_installs(tmp_path, monkeypatch):
    with local(tmp_path) as c:
        rt = c.app.state.rt
        rt.updater = make(api_ok(release("app-9")))
        assert c.post("/api/update/check", headers=H).json()["available"]
        st = c.get("/api/status").json()["update"]
        assert st["available"] and st["latest"] == "1.0.9" and st["can_install"] is False  # не установленная программа
        r = c.post("/api/update/install", headers=H)
        assert r.status_code == 400 and "установленной программе" in r.json()["detail"]
        # установленная программа: скачиваем, запускаем установщик, закрываемся
        monkeypatch.setattr(updater, "can_install", lambda: True)
        launched, stopped = [], []
        monkeypatch.setattr(updater.Updater, "launch", staticmethod(lambda p: launched.append(p)))
        c.app.state.quit = lambda: stopped.append(True)
        assert c.post("/api/update/install", headers=H).json() == {"ok": True}
        assert launched and launched[0].read_bytes() == BODY


def test_server_mode_has_no_update_buttons(client):
    client.post("/api/login", json={"password": "secret-pw"}, headers=H)
    assert client.get("/api/status").json()["update"] is None
    assert client.post("/api/update/check", headers=H).status_code == 404
    assert client.post("/api/update/install", headers=H).status_code == 404


def test_update_needs_the_csrf_header(tmp_path):
    with local(tmp_path) as c:
        assert c.post("/api/update/install").status_code == 403
